"""Locate and validate a dnglab executable."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def find_dnglab(configured_path: str | None = None) -> Path | None:
    """Search common locations for dnglab."""
    candidates: list[Path] = []

    if configured_path:
        candidate = Path(configured_path)
        if candidate.is_file():
            candidates.append(candidate)

    app_dir = Path(__file__).resolve().parent.parent.parent
    local_bundle = app_dir / "dnglab-win-x64_v0.7.2" / "dnglab.exe"
    if local_bundle.is_file():
        candidates.append(local_bundle)

    for name in ("dnglab.exe", "dnglab"):
        adjacent = app_dir / name
        if adjacent.is_file():
            candidates.append(adjacent)

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidate = Path(local_app_data) / "dnglab" / "dnglab.exe"
        if candidate.is_file():
            candidates.append(candidate)

    which_result = shutil.which("dnglab")
    if which_result:
        candidates.append(Path(which_result))

    for candidate in candidates:
        valid, _ = validate_dnglab(candidate)
        if valid:
            return candidate

    logger.warning("dnglab not found")
    return None


def validate_dnglab(path: Path) -> tuple[bool, str]:
    """Check whether a path points to a working dnglab executable."""
    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        version_line = result.stdout.strip() or result.stderr.strip()
        if result.returncode == 0 and "dnglab" in version_line.lower():
            return (True, version_line)
        return (False, version_line or f"exit {result.returncode}")
    except Exception as exc:
        return (False, str(exc))


def get_dnglab_version(path: Path) -> str:
    valid, text = validate_dnglab(path)
    return text if valid else "Unavailable"
