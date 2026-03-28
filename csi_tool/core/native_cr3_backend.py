"""Native Canon CR3 burst extraction backend.

This backend rebuilds single-image CR3 files directly from a CR3 burst/roll
container, so the app can extract raw `.CR3` frames without relying on
external tools.
"""

from __future__ import annotations

import logging
import mmap
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..utils.file_helpers import generate_output_filename

logger = logging.getLogger(__name__)


CANON_UUID = bytes.fromhex("85c0b687820f11e08111f4ce462b6a48")
XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
PRVW_UUID = bytes.fromhex("eaf42b5e1c984b88b9fbb7dc406e4d16")
CMTA_UUID = bytes.fromhex("5766b829bb6a47c5bcfb8b9f2260d06d")

CONTAINER_TYPES = {b"mdia", b"minf", b"moov", b"stbl", b"trak"}
SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}


class NativeCR3Error(Exception):
    """Raised when native CR3 extraction fails."""


@dataclass
class _Box:
    offset: int
    size: int
    box_type: bytes
    header_size: int

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass
class _StoredBox:
    data: bytes
    offset: int
    size: int


@dataclass
class _CTBOEntry:
    entry_index: int
    offset: int
    size: int
    target: str | None = None


@dataclass
class _Track:
    handler: str | None = None
    sample_sizes: list[int] = field(default_factory=list)
    sample_offsets: list[int] = field(default_factory=list)
    stsd_raw: bytes = b""
    tkhd_raw: bytes = b""
    mdhd_raw: bytes = b""
    hdlr_raw: bytes = b""
    vmhd_raw: bytes = b""
    nmhd_raw: bytes = b""
    dinf_raw: bytes = b""
    sample_size_is_fixed: bool = False


def _read_u16be(data: bytes | mmap.mmap, offset: int) -> int:
    return struct.unpack(">H", data[offset:offset + 2])[0]


def _read_u32be(data: bytes | mmap.mmap, offset: int) -> int:
    return struct.unpack(">I", data[offset:offset + 4])[0]


def _read_u64be(data: bytes | mmap.mmap, offset: int) -> int:
    return struct.unpack(">Q", data[offset:offset + 8])[0]


def _pack_u16be(value: int) -> bytes:
    return struct.pack(">H", value)


def _pack_u32be(value: int) -> bytes:
    return struct.pack(">I", value)


def _pack_u64be(value: int) -> bytes:
    return struct.pack(">Q", value)


def _iter_boxes(data: bytes | mmap.mmap, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        size = _read_u32be(data, pos)
        box_type = bytes(data[pos + 4:pos + 8])
        header_size = 8

        if size == 1:
            if pos + 16 > end:
                break
            size = _read_u64be(data, pos + 8)
            header_size = 16
        elif size == 0:
            size = end - pos

        if size < header_size or pos + size > end:
            break

        yield _Box(pos, size, box_type, header_size)
        pos += size


def _make_box(box_type: bytes | str, payload: bytes) -> bytes:
    if isinstance(box_type, str):
        box_type = box_type.encode("ascii")
    size = 8 + len(payload)
    if size > 0xFFFFFFFF:
        return _pack_u32be(1) + box_type + _pack_u64be(16 + len(payload)) + payload
    return _pack_u32be(size) + box_type + payload


def _make_full_box(box_type: bytes | str, version: int, flags: int, payload: bytes) -> bytes:
    return _make_box(box_type, _pack_u32be((version << 24) | flags) + payload)


def _make_uuid_box(uuid_bytes: bytes, payload: bytes) -> bytes:
    size = 24 + len(payload)
    if size > 0xFFFFFFFF:
        return _pack_u32be(1) + b"uuid" + _pack_u64be(24 + len(payload)) + uuid_bytes + payload
    return _pack_u32be(size) + b"uuid" + uuid_bytes + payload


def _jpeg_dimensions(jpeg_bytes: bytes) -> tuple[int, int] | None:
    if len(jpeg_bytes) < 4 or jpeg_bytes[:2] != b"\xFF\xD8":
        return None

    pos = 2
    length = len(jpeg_bytes)
    while pos + 4 <= length:
        while pos < length and jpeg_bytes[pos] != 0xFF:
            pos += 1
        while pos < length and jpeg_bytes[pos] == 0xFF:
            pos += 1
        if pos >= length:
            break

        marker = jpeg_bytes[pos]
        pos += 1

        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue

        if pos + 2 > length:
            break

        segment_size = _read_u16be(jpeg_bytes, pos)
        if segment_size < 2 or pos + segment_size > length:
            break

        if marker in SOF_MARKERS and pos + 7 <= length:
            height = _read_u16be(jpeg_bytes, pos + 3)
            width = _read_u16be(jpeg_bytes, pos + 5)
            return (width, height)

        pos += segment_size

    return None


class _BurstCR3File:
    """Read a burst CR3 file and rebuild single-image CR3 containers."""

    def __init__(self, input_path: Path):
        self.input_path = Path(input_path)
        self._file_handle = None
        self._mm: mmap.mmap | None = None
        self.ftyp: _StoredBox | None = None
        self.xmp_box: _StoredBox | None = None
        self.prvw_box: _StoredBox | None = None
        self.cmta_box: _StoredBox | None = None
        self.free_box: _StoredBox | None = None
        self.mdat_box: _Box | None = None
        self.mvhd_raw = b""
        self.canon_subboxes: list[bytes] = []
        self.ctbo_entries: list[_CTBOEntry] = []
        self.tracks: list[_Track] = []
        self.exposure_data: list[dict[str, int]] = []

    def __enter__(self) -> "_BurstCR3File":
        self._file_handle = open(self.input_path, "rb")
        self._mm = mmap.mmap(self._file_handle.fileno(), 0, access=mmap.ACCESS_READ)
        self._parse()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._file_handle is not None:
            self._file_handle.close()
            self._file_handle = None

    @property
    def image_count(self) -> int:
        counts = [
            min(len(track.sample_sizes), len(track.sample_offsets))
            for track in self.tracks
            if track.sample_sizes and track.sample_offsets
        ]
        return min(counts) if counts else 0

    def _parse(self) -> None:
        mm = self._require_mm()
        file_size = len(mm)
        moov_box: _Box | None = None

        for box in _iter_boxes(mm, 0, file_size):
            if box.box_type == b"ftyp":
                self.ftyp = _StoredBox(bytes(mm[box.offset:box.end]), box.offset, box.size)
            elif box.box_type == b"moov":
                moov_box = box
            elif box.box_type == b"mdat":
                self.mdat_box = box
            elif box.box_type == b"free":
                self.free_box = _StoredBox(bytes(mm[box.offset:box.end]), box.offset, box.size)
            elif box.box_type == b"uuid":
                uuid_offset = box.offset + box.header_size
                if uuid_offset + 16 > box.end:
                    continue
                uuid_value = bytes(mm[uuid_offset:uuid_offset + 16])
                stored = _StoredBox(bytes(mm[box.offset:box.end]), box.offset, box.size)
                if uuid_value == XMP_UUID:
                    self.xmp_box = stored
                elif uuid_value == PRVW_UUID:
                    self.prvw_box = stored
                elif uuid_value == CMTA_UUID:
                    self.cmta_box = stored

        if self.ftyp is None:
            raise NativeCR3Error(f"{self.input_path.name} is missing an ftyp box")
        if moov_box is None:
            raise NativeCR3Error(f"{self.input_path.name} is missing a moov box")
        if self.mdat_box is None:
            raise NativeCR3Error(f"{self.input_path.name} is missing an mdat box")

        self._parse_moov(moov_box)
        self._classify_ctbo_entries()
        self.exposure_data = self._parse_exposure_metadata()

        if self.image_count <= 1:
            raise NativeCR3Error(
                f"{self.input_path.name} does not look like a burst CR3 file"
            )

    def _parse_moov(self, moov_box: _Box) -> None:
        mm = self._require_mm()
        body_start = moov_box.offset + moov_box.header_size

        for child in _iter_boxes(mm, body_start, moov_box.end):
            if child.box_type == b"mvhd":
                self.mvhd_raw = bytes(mm[child.offset:child.end])
            elif child.box_type == b"trak":
                self.tracks.append(self._parse_track(child))
            elif child.box_type == b"uuid":
                uuid_offset = child.offset + child.header_size
                if uuid_offset + 16 > child.end:
                    continue
                uuid_value = bytes(mm[uuid_offset:uuid_offset + 16])
                if uuid_value == CANON_UUID:
                    self._parse_canon_uuid(child)

    def _parse_canon_uuid(self, uuid_box: _Box) -> None:
        mm = self._require_mm()
        content_start = uuid_box.offset + uuid_box.header_size + 16
        for child in _iter_boxes(mm, content_start, uuid_box.end):
            raw = bytes(mm[child.offset:child.end])
            self.canon_subboxes.append(raw)
            if child.box_type == b"CTBO":
                self.ctbo_entries = self._parse_ctbo(raw)

    def _parse_ctbo(self, ctbo_raw: bytes) -> list[_CTBOEntry]:
        if len(ctbo_raw) < 12:
            return []

        count = _read_u32be(ctbo_raw, 8)
        entries: list[_CTBOEntry] = []
        pos = 12
        for _ in range(count):
            if pos + 20 > len(ctbo_raw):
                break
            entries.append(
                _CTBOEntry(
                    entry_index=_read_u32be(ctbo_raw, pos),
                    offset=_read_u64be(ctbo_raw, pos + 4),
                    size=_read_u64be(ctbo_raw, pos + 12),
                )
            )
            pos += 20
        return entries

    def _classify_ctbo_entries(self) -> None:
        known_targets = {
            "xmp": self.xmp_box,
            "prvw": self.prvw_box,
            "cmta": self.cmta_box,
            "mdat": self.mdat_box,
        }
        for entry in self.ctbo_entries:
            for name, box in known_targets.items():
                if box is None:
                    continue
                offset = box.offset
                size = box.size
                if entry.offset == offset and entry.size == size:
                    entry.target = name
                    break

    def _parse_track(self, trak_box: _Box) -> _Track:
        mm = self._require_mm()
        track = _Track()

        def walk(start: int, end: int) -> None:
            for child in _iter_boxes(mm, start, end):
                raw = bytes(mm[child.offset:child.end])
                if child.box_type == b"tkhd":
                    track.tkhd_raw = raw
                elif child.box_type == b"mdhd":
                    track.mdhd_raw = raw
                elif child.box_type == b"hdlr":
                    track.hdlr_raw = raw
                    if len(raw) >= 20:
                        track.handler = raw[16:20].decode("ascii", errors="replace")
                elif child.box_type == b"vmhd":
                    track.vmhd_raw = raw
                elif child.box_type == b"nmhd":
                    track.nmhd_raw = raw
                elif child.box_type == b"dinf":
                    track.dinf_raw = raw
                elif child.box_type == b"stsd":
                    track.stsd_raw = raw
                elif child.box_type == b"stsz":
                    self._parse_stsz(raw, child.header_size, track)
                elif child.box_type == b"co64":
                    self._parse_co64(raw, child.header_size, track)
                elif child.box_type == b"stco":
                    self._parse_stco(raw, child.header_size, track)
                elif child.box_type in CONTAINER_TYPES:
                    walk(child.offset + child.header_size, child.end)

        walk(trak_box.offset + trak_box.header_size, trak_box.end)
        return track

    def _parse_stsz(self, raw: bytes, header_size: int, track: _Track) -> None:
        if len(raw) < header_size + 12:
            return

        sample_size = _read_u32be(raw, header_size + 4)
        sample_count = _read_u32be(raw, header_size + 8)
        track.sample_size_is_fixed = sample_size != 0

        if sample_size != 0:
            track.sample_sizes = [sample_size] * sample_count
            return

        sample_sizes = []
        pos = header_size + 12
        for _ in range(sample_count):
            if pos + 4 > len(raw):
                break
            sample_sizes.append(_read_u32be(raw, pos))
            pos += 4
        track.sample_sizes = sample_sizes

    def _parse_co64(self, raw: bytes, header_size: int, track: _Track) -> None:
        if len(raw) < header_size + 8:
            return

        entry_count = _read_u32be(raw, header_size + 4)
        offsets = []
        pos = header_size + 8
        for _ in range(entry_count):
            if pos + 8 > len(raw):
                break
            offsets.append(_read_u64be(raw, pos))
            pos += 8
        track.sample_offsets = offsets

    def _parse_stco(self, raw: bytes, header_size: int, track: _Track) -> None:
        if len(raw) < header_size + 8:
            return

        entry_count = _read_u32be(raw, header_size + 4)
        offsets = []
        pos = header_size + 8
        for _ in range(entry_count):
            if pos + 4 > len(raw):
                break
            offsets.append(_read_u32be(raw, pos))
            pos += 4
        track.sample_offsets = offsets

    def _parse_exposure_metadata(self) -> list[dict[str, int]]:
        metadata_track = next(
            (
                track for track in self.tracks
                if track.handler != "vide"
                and len(track.sample_offsets) >= self.image_count
                and len(track.sample_sizes) >= self.image_count
            ),
            None,
        )
        if metadata_track is None:
            return []

        mm = self._require_mm()
        exposure_data: list[dict[str, int]] = []
        for image_index in range(self.image_count):
            sample_offset = metadata_track.sample_offsets[image_index]
            sample_size = metadata_track.sample_sizes[image_index]
            record = bytes(mm[sample_offset:sample_offset + sample_size])
            info: dict[str, int] = {}
            pos = 0
            while pos + 12 <= len(record):
                record_size = struct.unpack("<I", record[pos:pos + 4])[0]
                record_type = struct.unpack("<I", record[pos + 4:pos + 8])[0]
                if record_size < 12 or pos + record_size > len(record):
                    break
                if record_type == 5 and record_size >= 24:
                    _f_num, _f_denom, _exp_num, _exp_denom, iso = struct.unpack(
                        "<HHHHL", record[pos + 12:pos + 24]
                    )
                    info["iso"] = iso
                pos += record_size
            exposure_data.append(info)
        return exposure_data

    def _patch_canon_subboxes(self, image_index: int) -> bytes:
        parts = []
        for subbox in self.canon_subboxes:
            box_type = subbox[4:8]
            if box_type == b"CCTP":
                patched = bytearray(subbox)
                if len(patched) >= 12:
                    patched[11] = 0x01
                parts.append(bytes(patched))
            elif box_type == b"CMT2":
                parts.append(self._patch_cmt2(subbox, image_index))
            elif box_type == b"CMT3":
                parts.append(self._patch_cmt3(subbox))
            else:
                parts.append(subbox)
        return _make_uuid_box(CANON_UUID, b"".join(parts))

    def _patch_cmt2(self, cmt2_raw: bytes, image_index: int) -> bytes:
        iso_value = 0
        if image_index < len(self.exposure_data):
            iso_value = self.exposure_data[image_index].get("iso", 0)
        if not iso_value or len(cmt2_raw) < 16:
            return cmt2_raw

        patched = bytearray(cmt2_raw)
        tiff_start = 8
        byte_order = patched[tiff_start:tiff_start + 2]
        if byte_order == b"II":
            fmt16 = "<H"
            fmt32 = "<I"
        elif byte_order == b"MM":
            fmt16 = ">H"
            fmt32 = ">I"
        else:
            return cmt2_raw

        ifd_offset = struct.unpack(fmt32, patched[tiff_start + 4:tiff_start + 8])[0]
        ifd_abs = tiff_start + ifd_offset
        if ifd_abs + 2 > len(patched):
            return cmt2_raw

        entry_count = struct.unpack(fmt16, patched[ifd_abs:ifd_abs + 2])[0]
        for entry_index in range(entry_count):
            entry_offset = ifd_abs + 2 + entry_index * 12
            if entry_offset + 12 > len(patched):
                break
            tag = struct.unpack(fmt16, patched[entry_offset:entry_offset + 2])[0]
            value_offset = entry_offset + 8
            if tag == 0x8827:
                patched[value_offset:value_offset + 4] = struct.pack(fmt16, iso_value) + b"\x00\x00"
            elif tag == 0x8832:
                patched[value_offset:value_offset + 4] = struct.pack(fmt32, iso_value)
        return bytes(patched)

    def _patch_cmt3(self, cmt3_raw: bytes) -> bytes:
        if len(cmt3_raw) < 16:
            return cmt3_raw

        patched = bytearray(cmt3_raw)
        tiff_start = 8
        byte_order = patched[tiff_start:tiff_start + 2]
        if byte_order == b"II":
            fmt16 = "<H"
            fmt32 = "<I"
        elif byte_order == b"MM":
            fmt16 = ">H"
            fmt32 = ">I"
        else:
            return cmt3_raw

        ifd_offset = struct.unpack(fmt32, patched[tiff_start + 4:tiff_start + 8])[0]
        ifd_abs = tiff_start + ifd_offset
        if ifd_abs + 2 > len(patched):
            return cmt3_raw

        entry_count = struct.unpack(fmt16, patched[ifd_abs:ifd_abs + 2])[0]
        for entry_index in range(entry_count):
            entry_offset = ifd_abs + 2 + entry_index * 12
            if entry_offset + 12 > len(patched):
                break

            tag = struct.unpack(fmt16, patched[entry_offset:entry_offset + 2])[0]
            value_type = struct.unpack(fmt16, patched[entry_offset + 2:entry_offset + 4])[0]
            count = struct.unpack(fmt32, patched[entry_offset + 4:entry_offset + 8])[0]
            if value_type != 4:
                continue

            value_ref = struct.unpack(fmt32, patched[entry_offset + 8:entry_offset + 12])[0]
            value_base = tiff_start + value_ref

            if tag == 0x403F and count == 3 and value_base + 12 <= len(patched):
                patched[value_base + 4:value_base + 8] = struct.pack(fmt32, 0)
                patched[value_base + 8:value_base + 12] = struct.pack(fmt32, 1)
            elif tag == 0x4040 and count == 10 and value_base + 40 <= len(patched):
                patched[value_base + 4:value_base + 8] = struct.pack(fmt32, 1)
                patched[value_base + 36:value_base + 40] = struct.pack(fmt32, 3)
        return bytes(patched)

    def _patch_mvhd(self, raw: bytes) -> bytes:
        patched = bytearray(raw)
        if len(patched) < 32:
            return raw

        version = patched[8]
        if version == 0 and len(patched) >= 28:
            patched[20:24] = _pack_u32be(1)
            patched[24:28] = _pack_u32be(1)
        elif version == 1 and len(patched) >= 40:
            patched[28:32] = _pack_u32be(1)
            patched[32:40] = _pack_u64be(1)
        return bytes(patched)

    def _patch_mdhd(self, raw: bytes) -> bytes:
        patched = bytearray(raw)
        if len(patched) < 32:
            return raw

        version = patched[8]
        if version == 0 and len(patched) >= 28:
            patched[20:24] = _pack_u32be(1)
            patched[24:28] = _pack_u32be(1)
        elif version == 1 and len(patched) >= 40:
            patched[28:32] = _pack_u32be(1)
            patched[32:40] = _pack_u64be(1)
        return bytes(patched)

    def _patch_tkhd(self, raw: bytes) -> bytes:
        patched = bytearray(raw)
        if len(patched) < 32:
            return raw

        version = patched[8]
        if version == 0 and len(patched) >= 32:
            patched[28:32] = _pack_u32be(1)
        elif version == 1 and len(patched) >= 44:
            patched[36:44] = _pack_u64be(1)
        return bytes(patched)

    def _build_stbl(self, track: _Track, image_index: int, sample_offset: int) -> bytes:
        if image_index >= len(track.sample_sizes):
            raise NativeCR3Error("Track sample table is missing the requested frame")

        sample_size = track.sample_sizes[image_index]
        stts = _make_full_box(
            "stts",
            0,
            0,
            _pack_u32be(1) + _pack_u32be(1) + _pack_u32be(1),
        )
        stsc = _make_full_box(
            "stsc",
            0,
            0,
            _pack_u32be(1) + _pack_u32be(1) + _pack_u32be(1) + _pack_u32be(1),
        )
        stsz = _make_full_box("stsz", 0, 0, _pack_u32be(sample_size) + _pack_u32be(1))
        co64 = _make_full_box("co64", 0, 0, _pack_u32be(1) + _pack_u64be(sample_offset))
        return _make_box("stbl", track.stsd_raw + stts + stsc + stsz + co64)

    def _build_trak(self, track: _Track, image_index: int, sample_offset: int) -> bytes:
        media_header = track.vmhd_raw or track.nmhd_raw
        if not (track.tkhd_raw and track.mdhd_raw and track.hdlr_raw and media_header and track.dinf_raw and track.stsd_raw):
            raise NativeCR3Error("Burst file is missing required track metadata")

        stbl = self._build_stbl(track, image_index, sample_offset)
        minf = _make_box("minf", media_header + track.dinf_raw + stbl)
        mdia = _make_box("mdia", self._patch_mdhd(track.mdhd_raw) + track.hdlr_raw + minf)
        return _make_box("trak", self._patch_tkhd(track.tkhd_raw) + mdia)

    def _build_moov(self, image_index: int, sample_offsets: list[int]) -> bytes:
        if not self.mvhd_raw:
            raise NativeCR3Error("Burst file is missing mvhd metadata")
        if len(sample_offsets) != len(self.tracks):
            raise NativeCR3Error("Internal sample offset count mismatch")

        moov_parts = [self._patch_canon_subboxes(image_index), self._patch_mvhd(self.mvhd_raw)]
        for track, sample_offset in zip(self.tracks, sample_offsets):
            moov_parts.append(self._build_trak(track, image_index, sample_offset))
        return _make_box("moov", b"".join(moov_parts))

    def _patch_ctbo(self, moov_raw: bytes, replacements: dict[str, tuple[int, int]]) -> bytes:
        if not self.ctbo_entries:
            return moov_raw

        patched = bytearray(moov_raw)
        ctbo_index = patched.find(b"CTBO")
        if ctbo_index < 4:
            return moov_raw

        ctbo_offset = ctbo_index - 4
        count = _read_u32be(patched, ctbo_offset + 8)
        pos = ctbo_offset + 12
        for _ in range(count):
            if pos + 20 > len(patched):
                break
            entry_index = _read_u32be(patched, pos)
            entry = next((item for item in self.ctbo_entries if item.entry_index == entry_index), None)
            if entry and entry.target in replacements:
                new_offset, new_size = replacements[entry.target]
                patched[pos + 4:pos + 12] = _pack_u64be(new_offset)
                patched[pos + 12:pos + 20] = _pack_u64be(new_size)
            pos += 20

        return bytes(patched)

    def _build_prvw_uuid(self, jpeg_bytes: bytes) -> bytes:
        if self.prvw_box is not None:
            original = self.prvw_box.data
            if len(original) >= 44:
                outer_header_size = 16 if _read_u32be(original, 0) == 1 else 8
                content_start = outer_header_size + 16
                content = original[content_start:]
                marker = content.find(b"PRVW")
                if marker >= 4:
                    subbox_offset = marker - 4
                    if subbox_offset + 20 <= len(content):
                        prefix = content[:subbox_offset]
                        header = content[subbox_offset + 8:subbox_offset + 20]
                        prvw_subbox = _make_box("PRVW", header + _pack_u32be(len(jpeg_bytes)) + jpeg_bytes)
                        return _make_uuid_box(PRVW_UUID, prefix + prvw_subbox)

        dimensions = _jpeg_dimensions(jpeg_bytes)
        width, height = dimensions or (1620, 1080)
        prefix = _pack_u32be(0) + _pack_u32be(1)
        prvw_payload = (
            _pack_u32be(0)
            + _pack_u16be(1)
            + _pack_u16be(width)
            + _pack_u16be(height)
            + _pack_u16be(1)
            + _pack_u32be(len(jpeg_bytes))
            + jpeg_bytes
        )
        return _make_uuid_box(PRVW_UUID, prefix + _make_box("PRVW", prvw_payload))

    def extract_image(self, image_index: int, output_path: Path) -> Path:
        if image_index < 0 or image_index >= self.image_count:
            raise NativeCR3Error(f"Frame index {image_index} is out of range")

        mm = self._require_mm()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        sample_slices: list[tuple[int, int]] = []
        sample_sizes: list[int] = []
        for track in self.tracks:
            if image_index >= len(track.sample_offsets) or image_index >= len(track.sample_sizes):
                raise NativeCR3Error("Burst sample tables do not match across tracks")
            offset = track.sample_offsets[image_index]
            size = track.sample_sizes[image_index]
            sample_slices.append((offset, size))
            sample_sizes.append(size)

        preview_jpeg = b""
        for sample_offset, sample_size in sample_slices:
            candidate = bytes(mm[sample_offset:sample_offset + sample_size])
            if candidate[:2] == b"\xFF\xD8":
                preview_jpeg = candidate
                break
        if not preview_jpeg:
            preview_jpeg = bytes(mm[sample_slices[0][0]:sample_slices[0][0] + sample_slices[0][1]])
        prvw_uuid = self._build_prvw_uuid(preview_jpeg)

        ordered_boxes = [
            ("ftyp", self.ftyp.data if self.ftyp else b""),
            ("moov", b""),
            ("xmp", self.xmp_box.data if self.xmp_box else b""),
            ("prvw", prvw_uuid),
            ("cmta", self.cmta_box.data if self.cmta_box else b""),
            ("free", self.free_box.data if self.free_box else b""),
        ]

        placeholder_offsets = [0] * len(self.tracks)
        moov = self._build_moov(image_index, placeholder_offsets)
        ordered_boxes[1] = ("moov", moov)

        bytes_before_mdat = sum(len(data) for _, data in ordered_boxes)
        mdat_payload_size = sum(sample_sizes)
        mdat_header_size = 16 if 8 + mdat_payload_size > 0xFFFFFFFF else 8
        mdat_offset = bytes_before_mdat
        mdat_data_start = mdat_offset + mdat_header_size

        current_offset = mdat_data_start
        rebuilt_offsets = []
        for size in sample_sizes:
            rebuilt_offsets.append(current_offset)
            current_offset += size

        moov = self._build_moov(image_index, rebuilt_offsets)
        ordered_boxes[1] = ("moov", moov)

        xmp_offset = len(ordered_boxes[0][1]) + len(moov)
        prvw_offset = xmp_offset + len(ordered_boxes[2][1])
        cmta_offset = prvw_offset + len(prvw_uuid)
        free_offset = cmta_offset + len(ordered_boxes[4][1])
        mdat_offset = free_offset + len(ordered_boxes[5][1])
        mdat_data_start = mdat_offset + mdat_header_size

        current_offset = mdat_data_start
        rebuilt_offsets = []
        for size in sample_sizes:
            rebuilt_offsets.append(current_offset)
            current_offset += size

        moov = self._build_moov(image_index, rebuilt_offsets)
        replacements = {
            "xmp": (xmp_offset, len(ordered_boxes[2][1])),
            "prvw": (prvw_offset, len(prvw_uuid)),
            "cmta": (cmta_offset, len(ordered_boxes[4][1])),
            "mdat": (mdat_offset, mdat_header_size + mdat_payload_size),
        }
        moov = self._patch_ctbo(moov, replacements)
        ordered_boxes[1] = ("moov", moov)

        with output_path.open("wb") as handle:
            for _, data in ordered_boxes:
                if data:
                    handle.write(data)

            mdat_box_size = 8 + mdat_payload_size
            if mdat_box_size > 0xFFFFFFFF:
                handle.write(_pack_u32be(1))
                handle.write(b"mdat")
                handle.write(_pack_u64be(16 + mdat_payload_size))
            else:
                handle.write(_pack_u32be(mdat_box_size))
                handle.write(b"mdat")

            for sample_offset, sample_size in sample_slices:
                handle.write(mm[sample_offset:sample_offset + sample_size])

        return output_path

    def _require_mm(self) -> mmap.mmap:
        if self._mm is None:
            raise NativeCR3Error("CR3 burst file is not open")
        return self._mm


class NativeCR3Backend:
    """Extract raw CR3 frames directly from Canon burst files."""

    def extract_all_frames(
        self,
        input_path: Path,
        output_dir: Path,
        progress_callback: Callable[[int, int, str], None] | None = None,
        expected_frames: int = 0,
    ) -> list[Path]:
        with _BurstCR3File(input_path) as burst:
            indices = list(range(burst.image_count))
            return self._extract_indices(
                burst,
                input_path,
                output_dir,
                indices,
                progress_callback,
            )

    def extract_frame_range(
        self,
        input_path: Path,
        output_dir: Path,
        indices: list[int],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[Path]:
        with _BurstCR3File(input_path) as burst:
            return self._extract_indices(
                burst,
                input_path,
                output_dir,
                indices,
                progress_callback,
            )

    def _extract_indices(
        self,
        burst: _BurstCR3File,
        input_path: Path,
        output_dir: Path,
        indices: list[int],
        progress_callback: Callable[[int, int, str], None] | None,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        total = len(indices)
        if total == 0:
            return []

        extracted: list[Path] = []
        for position, frame_index in enumerate(indices, start=1):
            if progress_callback:
                progress_callback(
                    position - 1,
                    total,
                    f"Frame {frame_index + 1} ({position}/{total})...",
                )

            output_name = generate_output_filename(
                input_path.stem,
                frame_index,
                burst.image_count,
                fmt="cr3",
            )
            output_path = output_dir / output_name
            if output_path.exists():
                extracted.append(output_path)
                continue

            burst.extract_image(frame_index, output_path)
            extracted.append(output_path)
            logger.info("Extracted raw frame %d -> %s", frame_index + 1, output_path.name)

        if progress_callback:
            progress_callback(total, total, f"Extracted {len(extracted)}/{total} frames")
        return extracted
