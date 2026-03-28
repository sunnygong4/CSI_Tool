"""Run the CSI web service with Uvicorn."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from ..main import setup_logging


def main() -> int:
    """Launch the FastAPI application."""
    setup_logging(Path(os.getenv("CSI_WEB_LOG_PATH", "data/csi_web.log")))
    host = os.getenv("CSI_WEB_HOST", "0.0.0.0")
    port = int(os.getenv("CSI_WEB_PORT", "8080"))
    uvicorn.run(
        "csi_tool.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
