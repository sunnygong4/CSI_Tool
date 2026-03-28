"""Data classes for CSI Tool."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FrameInfo:
    """Metadata for a single frame within a burst CR3 file."""
    index: int
    track_id: int
    offset: int          # Byte offset into mdat
    size: int            # Compressed frame size in bytes
    width: int = 0
    height: int = 0
    bit_depth: int = 14


@dataclass
class BurstFile:
    """Parsed representation of a Canon CR3 burst file."""
    path: Path
    filename: str
    file_size: int
    frame_count: int
    frames: list[FrameInfo] = field(default_factory=list)
    camera_model: str = "Unknown"
    capture_date: str = ""
    image_width: int = 0
    image_height: int = 0
    is_valid: bool = True
    error_message: str | None = None


@dataclass
class ExtractionJob:
    """A single extraction task."""
    burst_file: BurstFile
    frame_indices: list[int] = field(default_factory=list)  # empty = all
    output_dir: Path = field(default_factory=lambda: Path.cwd())
    output_format: str = "dng"
    status: str = "pending"       # pending, running, completed, failed
    progress: float = 0.0         # 0.0 to 1.0
    extracted_files: list[Path] = field(default_factory=list)
    error_message: str | None = None


@dataclass
class AppConfig:
    """Application configuration."""
    dnglab_path: str | None = None
    output_format: str = "dng"
    default_output_dir: str = ""
    output_subfolder_per_burst: bool = True
    output_naming: str = "original_prefix"  # 'sequential' or 'original_prefix'
    last_input_dir: str = ""
    last_output_dir: str = ""
