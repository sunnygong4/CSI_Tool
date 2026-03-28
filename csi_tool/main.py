"""Entry point for CSI Tool — Canon CR3 Burst File Extractor."""

import logging
import sys


def setup_logging():
    """Configure logging to console."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


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
