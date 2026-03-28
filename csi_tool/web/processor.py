"""Synchronous background job processor for the Ubuntu web service."""

from __future__ import annotations

import logging
import re
import shutil
import time
import zipfile
from pathlib import Path

from ..core.cr3_parser import CR3Parser
from ..core.dnglab_backend import DNGLabBackend, DNGLabError
from ..core.models import BurstFile
from ..core.native_cr3_backend import NativeCR3Backend, NativeCR3Error
from ..utils.dnglab_finder import get_dnglab_version, validate_dnglab
from .config import WebConfig
from .db import JobRecord, JobStore
from .storage import StorageBackend

logger = logging.getLogger(__name__)

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class UserFacingError(Exception):
    """Error type that can be shown directly to the user."""


def sanitize_name(value: str) -> str:
    """Sanitize a filename or stem for keys and archives."""
    cleaned = SAFE_NAME_RE.sub("_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "upload"


def build_source_key(config: WebConfig, job_id: str, original_filename: str) -> str:
    """Build the storage key for an uploaded CR3."""
    safe_name = sanitize_name(Path(original_filename).name)
    return f"{config.r2_upload_prefix}/{job_id}/{safe_name}"


def build_archive_name(original_filename: str, output_format: str) -> str:
    """Build the archive name returned to the user."""
    stem = sanitize_name(Path(original_filename).stem)
    return f"{stem}_{output_format}_frames.zip"


def build_result_key(config: WebConfig, job_id: str, archive_name: str) -> str:
    """Build the storage key for a ZIP result."""
    safe_name = sanitize_name(archive_name)
    return f"{config.r2_result_prefix}/{job_id}/{safe_name}"


class JobProcessor:
    """Process uploaded jobs and clean expired data."""

    def __init__(self, config: WebConfig, store: JobStore, storage: StorageBackend):
        self.config = config
        self.store = store
        self.storage = storage
        self.parser = CR3Parser()
        self.native_backend = NativeCR3Backend()
        self.dnglab_backend: DNGLabBackend | None = None
        self._last_progress_log: dict[str, tuple[int, str, int | None]] = {}
        self._initialize_runtime()

    def _initialize_runtime(self) -> None:
        self.config.ensure_runtime_dirs()
        if self.config.require_dnglab:
            self.dnglab_backend = self._load_dnglab_backend()

    def dnglab_status(self) -> tuple[bool, str]:
        """Return dnglab availability and version information."""
        try:
            backend = self.dnglab_backend or self._load_dnglab_backend()
        except RuntimeError as exc:
            return (False, str(exc))
        return (True, get_dnglab_version(backend.dnglab_path))

    def process_next_job(self) -> bool:
        """Claim and process one uploaded job if available."""
        job = self.store.claim_next_uploaded_job(int(time.time()))
        if job is None:
            return False
        self.process_job(job)
        return True

    def process_job(self, job: JobRecord) -> None:
        """Process a single job end to end."""
        workspace = self.config.work_root / job.id
        input_path = workspace / "input" / sanitize_name(job.original_filename)
        output_dir = workspace / "frames"
        archive_name = build_archive_name(job.original_filename, job.output_format)
        archive_path = workspace / archive_name
        now_ts = int(time.time())

        try:
            logger.info(
                "Processing job %s: %s -> %s",
                job.id,
                job.original_filename,
                job.output_format.upper(),
            )
            workspace.mkdir(parents=True, exist_ok=True)
            self._report_progress(job.id, 12, "Downloading source file...")
            self.storage.download_file(job.source_key, input_path)

            self._report_progress(job.id, 18, "Parsing burst metadata...")
            burst = self._parse_burst(input_path)
            self._report_progress(
                job.id,
                20,
                f"Found {burst.frame_count} burst frames.",
                frame_count=burst.frame_count,
            )

            extracted_files = self._extract_frames(job, burst, input_path, output_dir)
            self._report_progress(job.id, 88, "Creating ZIP archive...", frame_count=len(extracted_files))
            self._create_zip(archive_path, extracted_files)

            result_key = build_result_key(self.config, job.id, archive_name)
            self._report_progress(job.id, 95, "Uploading ZIP archive...", frame_count=len(extracted_files))
            self.storage.upload_file(archive_path, result_key, "application/zip")
            self.store.mark_completed(
                job.id,
                result_key=result_key,
                frame_count=len(extracted_files),
                completed_at=now_ts,
                expires_at=job.created_at + self.config.job_ttl_seconds,
            )
            logger.info("Completed web job %s", job.id)

        except UserFacingError as exc:
            self.store.mark_failed(job.id, error_message=str(exc), completed_at=int(time.time()))
            logger.warning("Job %s failed: %s", job.id, exc)
        except (NativeCR3Error, DNGLabError) as exc:
            message = self._safe_backend_error(exc)
            self.store.mark_failed(job.id, error_message=message, completed_at=int(time.time()))
            logger.warning("Job %s failed: %s", job.id, exc)
        except Exception:
            self.store.mark_failed(
                job.id,
                error_message="The server hit an unexpected error while processing this upload.",
                completed_at=int(time.time()),
            )
            logger.exception("Unexpected failure while processing job %s", job.id)
        finally:
            self._last_progress_log.pop(job.id, None)
            self._cleanup_workspace(workspace)

    def cleanup_expired_jobs(self) -> int:
        """Delete expired jobs and associated storage objects."""
        now_ts = int(time.time())
        expired_jobs = self.store.list_expired_jobs(now_ts)
        if not expired_jobs:
            self._cleanup_stale_workspaces(now_ts)
            return 0

        object_keys: list[str] = []
        job_ids: list[str] = []
        for job in expired_jobs:
            job_ids.append(job.id)
            object_keys.append(job.source_key)
            if job.result_key:
                object_keys.append(job.result_key)
            self._cleanup_workspace(self.config.work_root / job.id)

        self.storage.delete_objects(object_keys)
        self.store.delete_jobs(job_ids)
        self._cleanup_stale_workspaces(now_ts)
        logger.info("Cleaned %d expired web job(s)", len(job_ids))
        return len(job_ids)

    def _parse_burst(self, input_path: Path) -> BurstFile:
        burst = self.parser.parse(input_path)
        if not burst.is_valid:
            raise UserFacingError("The uploaded file could not be parsed as a Canon CR3 burst.")
        if burst.frame_count <= 1:
            raise UserFacingError("The uploaded file is not a burst CR3/CSI file.")
        return burst

    def _extract_frames(
        self,
        job: JobRecord,
        burst: BurstFile,
        input_path: Path,
        output_dir: Path,
    ) -> list[Path]:
        backend = self._get_backend(job.output_format)

        def on_progress(current: int, total: int, message: str) -> None:
            if total <= 0:
                progress_pct = 30
            else:
                progress_pct = 20 + int((current / total) * 60)
            self._report_progress(
                job.id,
                min(progress_pct, 85),
                message,
                frame_count=burst.frame_count,
            )

        if job.output_format == "dng":
            return backend.extract_all_frames(
                input_path,
                output_dir,
                on_progress,
                expected_frames=burst.frame_count,
            )
        return backend.extract_all_frames(input_path, output_dir, on_progress, expected_frames=burst.frame_count)

    def _get_backend(self, output_format: str):
        if output_format == "cr3":
            return self.native_backend
        if output_format != "dng":
            raise UserFacingError("Unsupported output format requested.")
        if self.dnglab_backend is None:
            self.dnglab_backend = self._load_dnglab_backend()
        return self.dnglab_backend

    def _load_dnglab_backend(self) -> DNGLabBackend:
        dnglab_path = Path(self.config.dnglab_path)
        valid, message = validate_dnglab(dnglab_path)
        if not valid:
            raise RuntimeError(f"dnglab is not available at {dnglab_path}: {message}")
        return DNGLabBackend(dnglab_path)

    @staticmethod
    def _create_zip(destination: Path, extracted_files: list[Path]) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(extracted_files):
                archive.write(file_path, arcname=file_path.name)

    @staticmethod
    def _safe_backend_error(exc: Exception) -> str:
        message = str(exc)
        if isinstance(exc, DNGLabError):
            return f"DNG conversion failed: {message}"
        return message or "Frame extraction failed."

    def _report_progress(
        self,
        job_id: str,
        progress_pct: int,
        progress_message: str,
        *,
        frame_count: int | None = None,
    ) -> None:
        self.store.update_progress(job_id, progress_pct, progress_message, frame_count=frame_count)

        signature = (progress_pct, progress_message, frame_count)
        if self._last_progress_log.get(job_id) == signature:
            return
        self._last_progress_log[job_id] = signature

        frame_suffix = f" | frames={frame_count}" if frame_count is not None else ""
        logger.info("Job %s [%s%%] %s%s", job_id, progress_pct, progress_message, frame_suffix)

    @staticmethod
    def _cleanup_workspace(workspace: Path) -> None:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)

    def _cleanup_stale_workspaces(self, now_ts: int) -> None:
        threshold = now_ts - self.config.job_ttl_seconds
        if not self.config.work_root.exists():
            return
        for child in self.config.work_root.iterdir():
            try:
                modified = int(child.stat().st_mtime)
            except FileNotFoundError:
                continue
            if modified <= threshold:
                self._cleanup_workspace(child)
