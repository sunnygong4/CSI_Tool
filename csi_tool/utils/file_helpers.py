"""File and path utilities."""

from pathlib import Path


def find_cr3_files(directory: Path) -> list[Path]:
    """Find all .CR3 files in a directory (non-recursive)."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.suffix.upper() == ".CR3" and p.is_file()
    )


def generate_output_filename(
    burst_stem: str,
    frame_index: int,
    total_frames: int,
    fmt: str = "cr3",
) -> str:
    """Generate output filename for an extracted frame.

    Example: CSI_2839_frame_0001.cr3
    """
    pad = max(4, len(str(total_frames)))
    return f"{burst_stem}_frame_{frame_index + 1:0{pad}d}.{fmt}"


def human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
