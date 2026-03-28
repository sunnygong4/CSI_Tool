"""Extraction orchestrator for Canon CR3 burst files."""

import logging
import threading
from typing import Callable

from .cr3_parser import CR3Parser
from .models import AppConfig, ExtractionJob
from .native_cr3_backend import NativeCR3Backend, NativeCR3Error

logger = logging.getLogger(__name__)


class Extractor:
    """Run burst extraction work in background threads for the UI and CLI."""

    def __init__(self, config: AppConfig, parser: CR3Parser):
        self.config = config
        self.parser = parser
        self.native_backend = NativeCR3Backend()
        self._cancel_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def extract(
        self,
        job: ExtractionJob,
        progress_callback: Callable[[int, int, str], None] | None = None,
        completion_callback: Callable[[ExtractionJob], None] | None = None,
    ) -> None:
        """Run extraction in a background thread."""
        self._cancel_event.clear()
        self._worker_thread = threading.Thread(
            target=self._extract_worker,
            args=(job, progress_callback, completion_callback),
            daemon=True,
        )
        self._worker_thread.start()

    def batch_extract(
        self,
        jobs: list[ExtractionJob],
        progress_callback: Callable[[int, int, str], None] | None = None,
        completion_callback: Callable[[list[ExtractionJob]], None] | None = None,
    ) -> None:
        """Process multiple burst files sequentially in a background thread."""
        self._cancel_event.clear()
        self._worker_thread = threading.Thread(
            target=self._batch_worker,
            args=(jobs, progress_callback, completion_callback),
            daemon=True,
        )
        self._worker_thread.start()

    def cancel(self) -> None:
        """Signal cancellation of ongoing extraction."""
        self._cancel_event.set()
        logger.info("Extraction cancellation requested")

    def _extract_worker(
        self,
        job: ExtractionJob,
        progress_callback: Callable | None,
        completion_callback: Callable | None,
    ) -> None:
        """Worker thread: extract a single burst file."""
        job.status = "running"
        try:
            if self._cancel_event.is_set():
                job.status = "failed"
                job.error_message = "Cancelled"
                return

            if job.frame_indices:
                extracted = self.native_backend.extract_frame_range(
                    job.burst_file.path,
                    job.output_dir,
                    job.frame_indices,
                    progress_callback,
                )
            else:
                extracted = self.native_backend.extract_all_frames(
                    job.burst_file.path,
                    job.output_dir,
                    progress_callback,
                    expected_frames=job.burst_file.frame_count,
                )

            job.extracted_files = extracted
            job.status = "completed"
            job.progress = 1.0

        except NativeCR3Error as exc:
            job.status = "failed"
            job.error_message = str(exc)
            logger.error("Extraction failed: %s", exc)
        except Exception as exc:
            job.status = "failed"
            job.error_message = f"Unexpected error: {exc}"
            logger.exception("Unexpected extraction error")
        finally:
            if completion_callback:
                completion_callback(job)

    def _batch_worker(
        self,
        jobs: list[ExtractionJob],
        progress_callback: Callable | None,
        completion_callback: Callable | None,
    ) -> None:
        """Worker thread: process multiple jobs sequentially."""
        total = len(jobs)
        for index, job in enumerate(jobs):
            if self._cancel_event.is_set():
                job.status = "failed"
                job.error_message = "Cancelled"
                continue

            def job_progress(current, total_frames, message):
                if progress_callback:
                    progress_callback(index, total, f"[{index + 1}/{total}] {message}")

            job.status = "running"
            try:
                if job.frame_indices:
                    extracted = self.native_backend.extract_frame_range(
                        job.burst_file.path,
                        job.output_dir,
                        job.frame_indices,
                        job_progress,
                    )
                else:
                    extracted = self.native_backend.extract_all_frames(
                        job.burst_file.path,
                        job.output_dir,
                        job_progress,
                        expected_frames=job.burst_file.frame_count,
                    )
                job.extracted_files = extracted
                job.status = "completed"
                job.progress = 1.0
            except NativeCR3Error as exc:
                job.status = "failed"
                job.error_message = str(exc)
                logger.error("Batch extraction failed for %s: %s", job.burst_file.filename, exc)
            except Exception as exc:
                job.status = "failed"
                job.error_message = f"Unexpected error: {exc}"
                logger.exception("Unexpected batch error for %s", job.burst_file.filename)

        if progress_callback:
            progress_callback(total, total, "Batch extraction complete")
        if completion_callback:
            completion_callback(jobs)
