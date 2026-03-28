"""Application configuration persistence."""

import json
import logging
from pathlib import Path

from ..core.models import AppConfig

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".csi_tool"
CONFIG_PATH = CONFIG_DIR / "config.json"


def get_default_config() -> AppConfig:
    """Return factory default configuration."""
    return AppConfig()


def load_config() -> AppConfig:
    """Load config from JSON file, return defaults if not found."""
    if not CONFIG_PATH.exists():
        return get_default_config()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return AppConfig(**{k: v for k, v in data.items() if hasattr(AppConfig, k)})
    except Exception as e:
        logger.warning("Failed to load config: %s. Using defaults.", e)
        return get_default_config()


def save_config(config: AppConfig) -> None:
    """Persist config to JSON file."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "default_output_dir": config.default_output_dir,
            "output_subfolder_per_burst": config.output_subfolder_per_burst,
            "output_naming": config.output_naming,
            "last_input_dir": config.last_input_dir,
            "last_output_dir": config.last_output_dir,
        }
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("Failed to save config: %s", e)
