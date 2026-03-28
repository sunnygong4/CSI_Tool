"""Command-line interface for CSI Tool."""

import argparse
import sys
from pathlib import Path

from ..core.cr3_parser import CR3Parser
from ..core.extractor import Extractor
from ..core.models import ExtractionJob
from ..utils.config import load_config
from ..utils.file_helpers import find_cr3_files, human_readable_size

FORMAT_NAMES = {
    "dng": "Adobe DNG",
    "cr3": "Canon CR3",
}


def format_name(output_format: str) -> str:
    """Return a user-facing label for an output format code."""
    return FORMAT_NAMES.get(output_format, output_format.upper())


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="csi_tool",
        description="CSI Tool - Canon CR3 burst extractor with Adobe DNG export",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    info_parser = subparsers.add_parser("info", help="Show burst file info")
    info_parser.add_argument("file", type=Path, help="Path to a CR3 burst file")

    extract_parser = subparsers.add_parser("extract", help="Extract frames from a burst file")
    extract_parser.add_argument("file", type=Path, help="Path to a CR3 burst file")
    extract_parser.add_argument("-o", "--output", type=Path, default=None, help="Output directory")
    extract_parser.add_argument(
        "-f",
        "--frames",
        type=str,
        default=None,
        help="Frame indices to extract, for example '1,3,5-10'. Default: all",
    )
    extract_parser.add_argument(
        "--format",
        choices=("dng", "cr3"),
        default=None,
        help="Output format. Defaults to Adobe DNG; Canon CR3 remains available.",
    )
    extract_parser.add_argument(
        "--dnglab",
        type=str,
        default=None,
        help="Optional path to dnglab.exe for DNG extraction",
    )

    batch_parser = subparsers.add_parser("batch", help="Extract all burst files in a directory")
    batch_parser.add_argument("directory", type=Path, help="Directory containing CR3 files")
    batch_parser.add_argument("-o", "--output", type=Path, default=None, help="Output directory")
    batch_parser.add_argument(
        "--format",
        choices=("dng", "cr3"),
        default=None,
        help="Output format. Defaults to Adobe DNG; Canon CR3 remains available.",
    )
    batch_parser.add_argument(
        "--dnglab",
        type=str,
        default=None,
        help="Optional path to dnglab.exe for DNG extraction",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "info":
        return cmd_info(args)
    if args.command == "extract":
        return cmd_extract(args)
    if args.command == "batch":
        return cmd_batch(args)
    return 0


def cmd_info(args) -> int:
    filepath = args.file
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        return 1

    burst = CR3Parser().parse(filepath)
    if not burst.is_valid:
        print(f"Error: {burst.error_message}", file=sys.stderr)
        return 1

    print(f"File:       {burst.filename}")
    print(f"Size:       {human_readable_size(burst.file_size)}")
    print(f"Camera:     {burst.camera_model}")
    print(f"Date:       {burst.capture_date}")
    print(f"Frames:     {burst.frame_count}")
    if burst.image_width and burst.image_height:
        print(f"Resolution: {burst.image_width} x {burst.image_height}")

    if burst.frames:
        print("\nFrame details:")
        for frame in burst.frames:
            print(
                f"  Frame {frame.index + 1:4d}: "
                f"offset=0x{frame.offset:012x}  "
                f"size={human_readable_size(frame.size)}"
            )

    return 0


def cmd_extract(args) -> int:
    filepath = args.file
    if not filepath.exists():
        print(f"Error: File not found: {filepath}", file=sys.stderr)
        return 1

    config = load_config()
    if args.dnglab:
        config.dnglab_path = args.dnglab
    if args.format:
        config.output_format = args.format

    parser = CR3Parser()
    extractor = Extractor(config, parser)
    burst = parser.parse(filepath)
    if not burst.is_valid:
        print(f"Error: {burst.error_message}", file=sys.stderr)
        return 1

    output_format = args.format or config.output_format or "dng"
    output_dir = args.output or filepath.parent / f"{filepath.stem}_extracted"
    frame_indices: list[int] = []

    print(f"Parsed {burst.filename}: {burst.frame_count} frames")
    print(f"Output format: {format_name(output_format)}")

    if args.frames:
        frame_indices = parse_frame_range(args.frames, burst.frame_count)
        if not frame_indices:
            print(f"Error: Invalid frame range: {args.frames}", file=sys.stderr)
            return 1
        print(f"Extracting {len(frame_indices)} selected frame(s)...")
    else:
        print(f"Extracting all {burst.frame_count} frames...")

    job = ExtractionJob(
        burst_file=burst,
        frame_indices=frame_indices,
        output_dir=output_dir,
        output_format=output_format,
    )

    extractor._extract_worker(job, lambda current, total, message: print(f"  {message}"), lambda _: None)

    if job.status == "completed":
        suffix = ".dng" if output_format == "dng" else ".cr3"
        print(f"\nDone! {len(job.extracted_files)} {suffix} files saved to: {output_dir}")
        return 0

    print(f"\nFailed: {job.error_message}", file=sys.stderr)
    return 1


def cmd_batch(args) -> int:
    directory = args.directory
    if not directory.is_dir():
        print(f"Error: Not a directory: {directory}", file=sys.stderr)
        return 1

    config = load_config()
    if args.dnglab:
        config.dnglab_path = args.dnglab
    if args.format:
        config.output_format = args.format

    parser = CR3Parser()
    extractor = Extractor(config, parser)
    output_format = args.format or config.output_format or "dng"

    cr3_files = find_cr3_files(directory)
    if not cr3_files:
        print(f"No .CR3 files found in {directory}")
        return 0

    burst_files = []
    for file_path in cr3_files:
        burst = parser.parse(file_path)
        if burst.is_valid and burst.frame_count > 1:
            burst_files.append(burst)
            print(f"  Found: {burst.filename} ({burst.frame_count} frames)")
        elif burst.is_valid:
            print(f"  Skip:  {file_path.name} (single frame, not a burst)")

    if not burst_files:
        print("No burst CR3 files found.")
        return 0

    print(f"\nExtracting {len(burst_files)} burst file(s) as {format_name(output_format)}...\n")

    failures = 0
    for burst in burst_files:
        output_dir = args.output or burst.path.parent / f"{burst.path.stem}_extracted"
        job = ExtractionJob(
            burst_file=burst,
            output_dir=output_dir,
            output_format=output_format,
        )

        def on_progress(current, total, message, _name=burst.filename):
            print(f"  [{_name}] {message}")

        extractor._extract_worker(job, on_progress, lambda _: None)

        if job.status == "completed":
            print(f"  {burst.filename}: {len(job.extracted_files)} frames -> {output_dir}\n")
        else:
            print(f"  {burst.filename}: FAILED - {job.error_message}\n", file=sys.stderr)
            failures += 1

    total = len(burst_files)
    print(f"Batch complete: {total - failures}/{total} succeeded")
    return 1 if failures else 0


def parse_frame_range(spec: str, max_frames: int) -> list[int]:
    """Parse a frame range spec like '1,3,5-10' into 0-based indices."""
    indices = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start_end = part.split("-", 1)
            try:
                start = int(start_end[0])
                end = int(start_end[1])
                for value in range(start, end + 1):
                    if 1 <= value <= max_frames:
                        indices.add(value - 1)
            except ValueError:
                return []
        else:
            try:
                value = int(part)
                if 1 <= value <= max_frames:
                    indices.add(value - 1)
            except ValueError:
                return []
    return sorted(indices)
