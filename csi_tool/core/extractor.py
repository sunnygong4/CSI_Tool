"""Extraction orchestrator for Canon CR3 burst files."""

import logging
import threading
from pathlib import Path
from typing import Callable

from .cr3_parser import CR3Parser
from .dnglab_backend import DNGLabBackend, DNGLabError
from .models import AppConfig, ExtractionJob
from .native_cr3_backend import NativeCR3Backend, NativeCR3Error
from ..utils.dnglab_finder import find_dnglab, validate_dnglab

logger = logging.getLogger(__name__)


class Extractor:
    """Run burst extraction work in background threads for the UI and CLI."""

    def __init__(self, config: AppConfig, parser: CR3Parser):
        self.config = config
        self.parser = parser
        self.native_backend = NativeCR3Backend()
        self.dnglab_backend: DNGLabBackend | None = None
        self._cancel_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._init_dnglab()

    @property
    def has_dnglab(self) -> bool:
        return self.dnglab_backend is not None

    @property
    def is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def _init_dnglab(self) -> None:
        path = find_dnglab(self.config.dnglab_path)
        if not path:
            self.dnglab_backend = None
            return
        self.dnglab_backend = DNGLabBackend(path)
        self.config.dnglab_path = str(path)
        logger.info("DNGLab initialized at %s", path)

    def set_dnglab_path(self, path: str) -> bool:
        candidate = Path(path)
        valid, _message = validate_dnglab(candidate)
        if not valid:
            return False
        self.dnglab_backend = DNGLabBackend(candidate)
        self.config.dnglab_path = str(candidate)
        return True

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
        self._cancel_event.set()
        logger.info("Extraction cancellation requested")

    def _get_backend(self, job: ExtractionJob):
        output_format = (job.output_format or self.config.output_format or "dng").lower()
        if output_format == "cr3":
            return self.native_backend
        if output_format == "dng":
            if self.dnglab_backend is None:
                raise DNGLabError(
                    "DNG output requires dnglab. Set a valid dnglab path in Settings."
                )
            return self.dnglab_backend
        raise ValueError(f"Unsupported output format: {output_format}")

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

            backend = self._get_backend(job)
            if job.frame_indices:
                extracted = backend.extract_frame_range(
                    job.burst_file.path,
                    job.output_dir,
                    job.frame_indices,
                    progress_callback,
                )
            else:
                extracted = backend.extract_all_frames(
                    job.burst_file.path,
                    job.output_dir,
                    progress_callback,
                    expected_frames=job.burst_file.frame_count,
                )

            job.extracted_files = extracted
            job.status = "completed"
            job.progress = 1.0

        except (NativeCR3Error, DNGLabError, ValueError) as exc:
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
                backend = self._get_backend(job)
                if job.frame_indices:
                    extracted = backend.extract_frame_range(
                        job.burst_file.path,
                        job.output_dir,
                        job.frame_indices,
                        job_progress,
                    )
                else:
                    extracted = backend.extract_all_frames(
                        job.burst_file.path,
                        job.output_dir,
                        job_progress,
                        expected_frames=job.burst_file.frame_count,
                    )
                job.extracted_files = extracted
                job.status = "completed"
                job.progress = 1.0
            except (NativeCR3Error, DNGLabError, ValueError) as exc:
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
