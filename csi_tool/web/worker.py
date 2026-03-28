"""Background worker loop for the Ubuntu web deployment."""

from __future__ import annotations

import logging
import signal
import threading
import time

from ..main import setup_logging
from .app import build_services

logger = logging.getLogger(__name__)


def main() -> int:
    """Run the worker loop until interrupted."""
    setup_logging()
    services = build_services()
    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        logger.info("Worker shutdown requested")
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    next_cleanup_at = time.time() + services.config.cleanup_interval_seconds
    logger.info("CSI worker started")

    while not stop_event.is_set():
        did_work = services.processor.process_next_job()

        now = time.time()
        if now >= next_cleanup_at:
            services.processor.cleanup_expired_jobs()
            next_cleanup_at = now + services.config.cleanup_interval_seconds

        if not did_work:
            stop_event.wait(services.config.worker_poll_seconds)

    services.processor.cleanup_expired_jobs()
    logger.info("CSI worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
