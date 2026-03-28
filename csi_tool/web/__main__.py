"""Run the CSI web service with Uvicorn."""

from __future__ import annotations

import os

import uvicorn


def main() -> int:
    """Launch the FastAPI application."""
    host = os.getenv("CSI_WEB_HOST", "0.0.0.0")
    port = int(os.getenv("CSI_WEB_PORT", "8080"))
    uvicorn.run("csi_tool.web.app:create_app", factory=True, host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
