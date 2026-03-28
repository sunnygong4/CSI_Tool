"""DNGLab subprocess wrapper for burst-to-DNG extraction."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from .native_cr3_backend import NativeCR3Backend
from ..utils.file_helpers import generate_output_filename

logger = logging.getLogger(__name__)

TIMEOUT_PER_FRAME = 60
TIMEOUT_BASE = 120


class DNGLabError(Exception):
    """Raised when dnglab fails."""


class DNGLabBackend:
    """Wrap the dnglab CLI for extracting burst frames as DNG files."""

    def __init__(self, dnglab_path: Path):
        self.dnglab_path = Path(dnglab_path)
        self.native_backend = NativeCR3Backend()

    def extract_all_frames(
        self,
        input_path: Path,
        output_dir: Path,
        progress_callback: Callable[[int, int, str], None] | None = None,
        expected_frames: int = 0,
    ) -> list[Path]:
        if expected_frames <= 0:
            raise DNGLabError("Cannot extract DNGs without a known frame count.")

        indices = list(range(expected_frames))
        return self.extract_frame_range(input_path, output_dir, indices, progress_callback)

    def extract_frame_range(
        self,
        input_path: Path,
        output_dir: Path,
        indices: list[int],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        extracted: list[Path] = []
        total = len(indices)

        for position, frame_index in enumerate(indices, start=1):
            logger.info("Starting DNG frame %d/%d (source index %d)", position, total, frame_index + 1)
            if progress_callback:
                progress_callback(position - 1, total, f"Frame {frame_index + 1} ({position}/{total})...")

            extracted.append(
                self.extract_single_frame(
                    input_path,
                    output_dir,
                    frame_index,
                    total_frames=max(indices) + 1 if indices else 0,
                )
            )
            logger.info("Finished DNG frame %d/%d", position, total)

        if progress_callback:
            progress_callback(total, total, f"Extracted {len(extracted)}/{total} frames")

        return extracted

    def extract_single_frame(
        self,
        input_path: Path,
        output_dir: Path,
        frame_index: int,
        total_frames: int = 0,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)

        final_name = generate_output_filename(
            input_path.stem,
            frame_index,
            total_frames if total_frames > 0 else frame_index + 1,
            fmt="dng",
        )
        final_path = output_dir / final_name
        if final_path.exists():
            return final_path

        with tempfile.TemporaryDirectory(dir=output_dir, prefix=".csi_intermediate_") as temp_dir:
            temp_dir_path = Path(temp_dir)
            logger.info("Extracting intermediate CR3 for frame %d", frame_index + 1)
            temp_cr3 = self.native_backend.extract_frame_range(
                input_path,
                temp_dir_path,
                [frame_index],
            )[0]

            args = [
                str(self.dnglab_path),
                "convert",
                "-f",
                "-v",
                str(temp_cr3),
                str(output_dir),
            ]

            timeout = TIMEOUT_BASE + TIMEOUT_PER_FRAME
            logger.info("Converting frame %d CR3 -> DNG with dnglab", frame_index + 1)
            self._run_dnglab_streaming(args, timeout)

            dnglab_output = output_dir / f"{temp_cr3.stem}.dng"
            if dnglab_output.exists():
                if dnglab_output != final_path:
                    dnglab_output.rename(final_path)
                return final_path

            candidates = sorted(
                path
                for path in output_dir.iterdir()
                if path.suffix.lower() == ".dng" and path.name != final_name
            )
            if candidates:
                candidates[-1].rename(final_path)
                return final_path

        raise DNGLabError(f"Failed to extract frame {frame_index + 1} from {input_path.name}")

    def _run_dnglab_streaming(self, args: list[str], timeout: int) -> None:
        logger.info("Running: %s", " ".join(args))
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise DNGLabError(f"dnglab not found at: {self.dnglab_path}") from exc

        errors: list[str] = []
        start = time.monotonic()

        try:
            assert proc.stderr is not None
            for raw_line in proc.stderr:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if time.monotonic() - start > timeout:
                    proc.kill()
                    raise DNGLabError(f"dnglab timed out after {timeout}s")
                if not line:
                    continue
                if "error" in line.lower() or "fatal" in line.lower():
                    errors.append(line)
                    logger.error("dnglab error: %s", line)
                else:
                    logger.info("dnglab: %s", line)

            proc.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            raise DNGLabError("dnglab timed out waiting for exit") from exc
        except Exception:
            proc.kill()
            raise

        if proc.returncode != 0:
            detail = "; ".join(errors) if errors else f"exit {proc.returncode}"
            raise DNGLabError(f"dnglab failed: {detail}")
