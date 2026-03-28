"""Entry point for CSI Tool — Canon CR3 Burst File Extractor."""

import logging
import sys
from pathlib import Path


def setup_logging(log_path: str | Path | None = None):
    """Configure logging to console and optionally a shared file."""
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()
    root_logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_path:
        resolved_path = Path(log_path)
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(resolved_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def main():
    setup_logging()

    # If CLI args are present, run in CLI mode
    if len(sys.argv) > 1:
        from .cli.cli import main as cli_main
        sys.exit(cli_main())
    else:
        # Launch GUI
        from .gui.app import CSIToolApp
        app = CSIToolApp()
        app.run()


if __name__ == "__main__":
    main()
