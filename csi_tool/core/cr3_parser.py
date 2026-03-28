"""Pure Python ISOBMFF parser for Canon CR3 burst files.

Parses the moov box hierarchy to extract frame count, offsets, and metadata
without reading the (large) mdat payload into memory.

Reference: https://github.com/lclevy/canon_cr3
"""

import logging
import mmap
import struct
from pathlib import Path

from .models import BurstFile, FrameInfo

logger = logging.getLogger(__name__)

# ISOBMFF box types we care about
CONTAINER_BOXES = {
    b"moov", b"trak", b"mdia", b"minf", b"stbl", b"dinf",
    b"edts", b"udta", b"moof", b"traf", b"skip",
}

# Canon-specific container boxes
CANON_CONTAINER_BOXES = {
    b"CMT1", b"CMT2", b"CMT3", b"CMT4", b"CMTA",
    b"CRAW", b"CDI1", b"CMP1", b"IAD1",
}

# EXIF tag IDs we want from CMT1 (IFD0)
EXIF_TAG_MODEL = 0x0110
EXIF_TAG_DATETIME = 0x0132


class CR3ParseError(Exception):
    """Raised when a CR3 file cannot be parsed."""


class CR3Parser:
    """Parse Canon CR3 ISOBMFF container to extract burst frame metadata."""

    def parse(self, filepath: Path) -> BurstFile:
        """Parse a CR3 file and return burst file metadata with frame info."""
        filepath = Path(filepath)
        file_size = filepath.stat().st_size

        burst = BurstFile(
            path=filepath,
            filename=filepath.name,
            file_size=file_size,
            frame_count=0,
        )

        try:
            with open(filepath, "rb") as f:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    self._parse_file(mm, burst)
                finally:
                    mm.close()
        except CR3ParseError as e:
            burst.is_valid = False
            burst.error_message = str(e)
            logger.error("Failed to parse %s: %s", filepath.name, e)
        except Exception as e:
            burst.is_valid = False
            burst.error_message = f"Unexpected error: {e}"
            logger.exception("Unexpected error parsing %s", filepath.name)

        return burst

    def is_burst_file(self, filepath: Path) -> bool:
        """Quick check whether a CR3 file contains multiple frames."""
        try:
            burst = self.parse(filepath)
            return burst.is_valid and burst.frame_count > 1
        except Exception:
            return False

    def _parse_file(self, mm: mmap.mmap, burst: BurstFile) -> None:
        """Parse top-level boxes in the file."""
        size = len(mm)
        offset = 0

        # Context collected during parsing
        self._tracks: list[dict] = []
        self._current_track: dict | None = None
        self._camera_model = ""
        self._capture_date = ""
        self._image_width = 0
        self._image_height = 0

        # Validate ftyp
        if size < 12:
            raise CR3ParseError("File too small to be a valid CR3")

        box_size, box_type = self._read_box_header(mm, 0)
        if box_type != b"ftyp":
            raise CR3ParseError(f"Expected ftyp box, got {box_type!r}")

        brand = mm[8:12]
        if brand != b"crx ":
            raise CR3ParseError(f"Not a CR3 file (brand={brand!r})")

        # Parse all top-level boxes
        while offset < size:
            box_size, box_type = self._read_box_header(mm, offset)
            if box_size == 0:
                box_size = size - offset  # box extends to EOF
            if box_size < 8:
                break

            body_offset = offset + 8
            if box_size > 8 and mm[offset + 4:offset + 8] == b"uuid":
                body_offset = offset + 24  # UUID box has 16-byte UUID after type

            if box_type == b"moov":
                self._parse_container(mm, offset + 8, offset + box_size)
            elif box_type == b"mdat":
                pass  # We don't read mdat, just use offsets from moov

            offset += box_size

        # Find the full-resolution CRAW track (largest dimensions).
        # CR3 burst files have multiple tracks:
        #   Track 0: CRAW/JPEG preview (1620x1080)
        #   Track 1: CRAW/CMP1 small raw (1620x1080)
        #   Track 2: CRAW/CMP1 full-res raw (6000x4000)
        #   Track 3: CTMD metadata
        # We want the track with the largest width*height that has samples.
        best_track = None
        best_pixels = 0
        for track in self._tracks:
            sample_count = len(track.get("sample_sizes", []))
            if sample_count == 0:
                continue
            w = track.get("width", 0)
            h = track.get("height", 0)
            pixels = w * h
            if pixels > best_pixels:
                best_pixels = pixels
                best_track = track
            elif pixels == best_pixels and best_track is None:
                best_track = track

        # Fallback: if no track has dimensions, pick the one with most samples
        if best_track is None:
            for track in self._tracks:
                sample_count = len(track.get("sample_sizes", []))
                if best_track is None or sample_count > len(best_track.get("sample_sizes", [])):
                    best_track = track

        if best_track and len(best_track.get("sample_sizes", [])) > 0:
            offsets = best_track.get("chunk_offsets", [])
            sizes = best_track.get("sample_sizes", [])
            width = best_track.get("width", 0)
            height = best_track.get("height", 0)
            bit_depth = best_track.get("bit_depth", 14)
            track_id = best_track.get("track_id", 0)

            # Build frame list — pair up offsets and sizes
            frame_count = min(len(offsets), len(sizes))
            frames = []
            for i in range(frame_count):
                frames.append(FrameInfo(
                    index=i,
                    track_id=track_id,
                    offset=offsets[i],
                    size=sizes[i],
                    width=width,
                    height=height,
                    bit_depth=bit_depth,
                ))

            burst.frame_count = frame_count
            burst.frames = frames
            burst.image_width = width
            burst.image_height = height

        burst.camera_model = self._camera_model
        burst.capture_date = self._capture_date

        # Cleanup
        del self._tracks
        del self._current_track

    def _read_box_header(self, mm: mmap.mmap, offset: int) -> tuple[int, bytes]:
        """Read box size (4 bytes) and type (4 bytes). Handle extended size."""
        if offset + 8 > len(mm):
            return (0, b"\x00\x00\x00\x00")

        size = struct.unpack(">I", mm[offset:offset + 4])[0]
        box_type = mm[offset + 4:offset + 8]

        if size == 1 and offset + 16 <= len(mm):
            # 64-bit extended size
            size = struct.unpack(">Q", mm[offset + 8:offset + 16])[0]

        return (size, box_type)

    def _parse_container(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Recursively parse a container box's children."""
        offset = start
        while offset < end - 8:
            box_size, box_type = self._read_box_header(mm, offset)
            if box_size == 0:
                break
            if box_size < 8:
                break
            if offset + box_size > end:
                break

            body_start = offset + 8
            box_end = offset + box_size

            # Handle extended size header
            if struct.unpack(">I", mm[offset:offset + 4])[0] == 1:
                body_start = offset + 16

            handler = self._get_handler(box_type)
            if handler:
                handler(mm, body_start, box_end)
            elif box_type in CONTAINER_BOXES or box_type in CANON_CONTAINER_BOXES:
                self._parse_container(mm, body_start, box_end)

            offset = box_end

    def _get_handler(self, box_type: bytes):
        """Return a handler method for known box types, or None."""
        handlers = {
            b"trak": self._handle_trak,
            b"tkhd": self._handle_tkhd,
            b"stsd": self._handle_stsd,
            b"stsz": self._handle_stsz,
            b"co64": self._handle_co64,
            b"stco": self._handle_stco,
            b"CMT1": self._handle_cmt1,
        }
        return handlers.get(box_type)

    def _handle_trak(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Start parsing a new track."""
        self._current_track = {
            "track_id": len(self._tracks) + 1,
            "sample_sizes": [],
            "chunk_offsets": [],
            "width": 0,
            "height": 0,
            "bit_depth": 14,
        }
        self._parse_container(mm, start, end)
        self._tracks.append(self._current_track)
        self._current_track = None

    def _handle_tkhd(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Parse track header for track ID and dimensions."""
        if self._current_track is None or end - start < 4:
            return

        version = mm[start]
        if version == 0:
            # Version 0: 4+4+4+4+... bytes
            if end - start >= 84:
                track_id = struct.unpack(">I", mm[start + 12:start + 16])[0]
                width = struct.unpack(">I", mm[start + 76:start + 80])[0] >> 16
                height = struct.unpack(">I", mm[start + 80:start + 84])[0] >> 16
                self._current_track["track_id"] = track_id
                if width > 0 and height > 0:
                    self._current_track["width"] = width
                    self._current_track["height"] = height
        elif version == 1:
            # Version 1: 8+8+4+4+... bytes
            if end - start >= 96:
                track_id = struct.unpack(">I", mm[start + 20:start + 24])[0]
                width = struct.unpack(">I", mm[start + 88:start + 92])[0] >> 16
                height = struct.unpack(">I", mm[start + 92:start + 96])[0] >> 16
                self._current_track["track_id"] = track_id
                if width > 0 and height > 0:
                    self._current_track["width"] = width
                    self._current_track["height"] = height

    def _handle_stsd(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Parse sample description box — look for CRAW entry with dimensions."""
        if self._current_track is None or end - start < 16:
            return

        # stsd is a full box: version(1) + flags(3) + entry_count(4)
        entry_count = struct.unpack(">I", mm[start + 4:start + 8])[0]
        if entry_count == 0:
            return

        # First entry starts at offset 8
        entry_start = start + 8
        if entry_start + 8 > end:
            return

        entry_size = struct.unpack(">I", mm[entry_start:entry_start + 4])[0]
        entry_type = mm[entry_start + 4:entry_start + 8]

        if entry_type == b"CRAW":
            # CRAW sample entry: skip 6 reserved + 2 data_ref_idx + 16 predefined + 2 width + 2 height
            craw_body = entry_start + 8
            if craw_body + 26 <= end:
                # Width and height at offset 24 and 26 from entry body
                w = struct.unpack(">H", mm[craw_body + 24:craw_body + 26])[0]
                h = struct.unpack(">H", mm[craw_body + 26:craw_body + 28])[0]
                if w > 0 and h > 0:
                    self._current_track["width"] = w
                    self._current_track["height"] = h

            # Parse children of CRAW for CMP1
            child_offset = craw_body + 70  # Skip sample entry header fields
            if child_offset < entry_start + entry_size:
                self._parse_craw_children(mm, child_offset, entry_start + entry_size)

    def _parse_craw_children(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Parse child boxes inside CRAW sample entry for CMP1 etc."""
        offset = start
        while offset + 8 <= end:
            box_size, box_type = self._read_box_header(mm, offset)
            if box_size < 8 or offset + box_size > end:
                break

            if box_type == b"CMP1" and self._current_track is not None:
                # CMP1 contains compression parameters
                body = offset + 8
                if body + 12 <= end:
                    # Bit depth is typically at a known offset in CMP1
                    pass  # We already get dimensions from CRAW header

            offset += box_size

    def _handle_stsz(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Parse sample size box — one size per frame."""
        if self._current_track is None or end - start < 12:
            return

        # stsz: version(1) + flags(3) + sample_size(4) + sample_count(4)
        sample_size = struct.unpack(">I", mm[start + 4:start + 8])[0]
        sample_count = struct.unpack(">I", mm[start + 8:start + 12])[0]

        if sample_size != 0:
            # All samples are the same size
            self._current_track["sample_sizes"] = [sample_size] * sample_count
        else:
            # Variable size — read per-sample sizes
            sizes = []
            data_start = start + 12
            for i in range(sample_count):
                pos = data_start + i * 4
                if pos + 4 > end:
                    break
                sizes.append(struct.unpack(">I", mm[pos:pos + 4])[0])
            self._current_track["sample_sizes"] = sizes

    def _handle_co64(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Parse 64-bit chunk offset box."""
        if self._current_track is None or end - start < 8:
            return

        # co64: version(1) + flags(3) + entry_count(4)
        entry_count = struct.unpack(">I", mm[start + 4:start + 8])[0]
        offsets = []
        data_start = start + 8
        for i in range(entry_count):
            pos = data_start + i * 8
            if pos + 8 > end:
                break
            offsets.append(struct.unpack(">Q", mm[pos:pos + 8])[0])
        self._current_track["chunk_offsets"] = offsets

    def _handle_stco(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Parse 32-bit chunk offset box (fallback for co64)."""
        if self._current_track is None or end - start < 8:
            return

        entry_count = struct.unpack(">I", mm[start + 4:start + 8])[0]
        offsets = []
        data_start = start + 8
        for i in range(entry_count):
            pos = data_start + i * 4
            if pos + 4 > end:
                break
            offsets.append(struct.unpack(">I", mm[pos:pos + 4])[0])
        self._current_track["chunk_offsets"] = offsets

    def _handle_cmt1(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Parse CMT1 box (EXIF IFD0) for camera model and date."""
        # CMT1 contains a TIFF/EXIF structure
        if end - start < 14:
            return

        # Try to find EXIF IFD0 entries
        try:
            self._parse_exif_ifd0(mm, start, end)
        except Exception:
            pass  # Non-critical, skip if EXIF parsing fails

    def _parse_exif_ifd0(self, mm: mmap.mmap, start: int, end: int) -> None:
        """Parse EXIF IFD0 to extract camera model and datetime."""
        # TIFF header: byte order (2) + magic 42 (2) + IFD0 offset (4)
        if end - start < 8:
            return

        byte_order = mm[start:start + 2]
        if byte_order == b"MM":
            fmt_prefix = ">"  # Big endian
        elif byte_order == b"II":
            fmt_prefix = "<"  # Little endian
        else:
            return

        magic = struct.unpack(f"{fmt_prefix}H", mm[start + 2:start + 4])[0]
        if magic != 42:
            return

        ifd_offset = struct.unpack(f"{fmt_prefix}I", mm[start + 4:start + 8])[0]
        ifd_abs = start + ifd_offset

        if ifd_abs + 2 > end:
            return

        num_entries = struct.unpack(f"{fmt_prefix}H", mm[ifd_abs:ifd_abs + 2])[0]

        for i in range(num_entries):
            entry_pos = ifd_abs + 2 + i * 12
            if entry_pos + 12 > end:
                break

            tag = struct.unpack(f"{fmt_prefix}H", mm[entry_pos:entry_pos + 2])[0]
            type_id = struct.unpack(f"{fmt_prefix}H", mm[entry_pos + 2:entry_pos + 4])[0]
            count = struct.unpack(f"{fmt_prefix}I", mm[entry_pos + 4:entry_pos + 8])[0]
            value_offset = struct.unpack(f"{fmt_prefix}I", mm[entry_pos + 8:entry_pos + 12])[0]

            if tag in (EXIF_TAG_MODEL, EXIF_TAG_DATETIME):
                # ASCII string type (2), data is at offset if > 4 bytes
                if type_id == 2 and count > 0:
                    if count <= 4:
                        data = mm[entry_pos + 8:entry_pos + 8 + count]
                    else:
                        str_pos = start + value_offset
                        if str_pos + count <= end:
                            data = mm[str_pos:str_pos + count]
                        else:
                            continue
                    text = data.rstrip(b"\x00").decode("ascii", errors="replace").strip()
                    if tag == EXIF_TAG_MODEL:
                        self._camera_model = text
                    elif tag == EXIF_TAG_DATETIME:
                        self._capture_date = text
