"""FastAPI application for the CSI Ubuntu web service."""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from ..utils.file_helpers import human_readable_size
from .auth import create_signed_token, hash_client_ip, hash_secret_value, verify_signed_token
from .config import WebConfig
from .db import JobRecord, JobStore
from .processor import JobProcessor, build_archive_name, build_source_key
from .storage import DownloadTarget, LocalStorage, R2Storage, StorageBackend, UploadTarget

logger = logging.getLogger(__name__)

ADMIN_COOKIE_NAME = "csi_admin_session"
ADMIN_COOKIE_PURPOSE = "admin"

FORMAT_LABELS = {
    "dng": "Adobe DNG",
    "cr3": "Canon CR3",
}


class InitiateJobRequest(BaseModel):
    """Request body for creating a new extraction job."""

    filename: str
    file_size: int
    output_format: str = "dng"


@dataclass(slots=True)
class AppServices:
    """Runtime services shared by the app and worker."""

    config: WebConfig
    store: JobStore
    storage: StorageBackend
    processor: JobProcessor
    templates: Jinja2Templates


def build_storage(config: WebConfig) -> StorageBackend:
    """Build the configured storage backend."""
    if config.storage_backend == "local":
        return LocalStorage(config.local_storage_root, app_secret=config.app_secret)
    return R2Storage(
        endpoint_url=config.r2_endpoint_url,
        region_name=config.r2_region_name,
        access_key_id=config.r2_access_key_id,
        secret_access_key=config.r2_secret_access_key,
        bucket_name=config.r2_bucket_name,
    )


def build_services(config: WebConfig | None = None) -> AppServices:
    """Create the shared services used by the web app and worker."""
    config = config or WebConfig.from_env()
    config.ensure_runtime_dirs()

    store = JobStore(config.database_path)
    store.initialize()
    storage = build_storage(config)
    processor = JobProcessor(config, store, storage)

    templates_dir = Path(__file__).with_name("templates")
    templates = Jinja2Templates(directory=str(templates_dir))
    return AppServices(config=config, store=store, storage=storage, processor=processor, templates=templates)


def create_app(config: WebConfig | None = None) -> FastAPI:
    """FastAPI application factory."""
    services = build_services(config)
    app = FastAPI(title=services.config.app_name)
    app.state.services = services

    static_dir = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return services.templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "domain_name": services.config.public_base_url,
                "format_labels": FORMAT_LABELS,
                "default_format": services.config.default_output_format,
                "max_upload_bytes": services.config.upload_max_bytes,
                "max_upload_label": human_readable_size(services.config.upload_max_bytes),
                "job_ttl_minutes": services.config.job_ttl_seconds // 60,
                "storage_backend": services.config.storage_backend,
            },
        )

    @app.get("/log", response_class=HTMLResponse)
    async def log_view(request: Request, notice: str | None = None, error: str | None = None):
        authenticated = _is_admin_authenticated(request)
        recent_logs = _read_log_tail(services.config.log_path, services.config.log_tail_lines) if authenticated else []
        jobs = services.store.list_jobs()[:20] if authenticated else []
        return services.templates.TemplateResponse(
            request=request,
            name="log.html",
            context={
                "authenticated": authenticated,
                "notice": notice,
                "error": error,
                "recent_logs": recent_logs,
                "log_path": str(services.config.log_path),
                "log_tail_lines": services.config.log_tail_lines,
                "jobs": jobs,
            },
            status_code=200 if authenticated else 401,
        )

    @app.post("/log/login")
    async def log_login(request: Request, token: str = Form(...)):
        if token != services.config.effective_admin_token:
            return services.templates.TemplateResponse(
                request=request,
                name="log.html",
                context={
                    "authenticated": False,
                    "notice": None,
                    "error": "Admin token was incorrect.",
                    "recent_logs": [],
                    "log_path": str(services.config.log_path),
                    "log_tail_lines": services.config.log_tail_lines,
                    "jobs": [],
                },
                status_code=403,
            )

        expires_at = int(time.time()) + (services.config.admin_session_hours * 3600)
        session_token = create_signed_token(
            services.config.app_secret,
            ADMIN_COOKIE_PURPOSE,
            hash_secret_value(services.config.effective_admin_token),
            expires_at,
        )
        response = RedirectResponse(url="/log?notice=Signed+in", status_code=303)
        response.set_cookie(
            ADMIN_COOKIE_NAME,
            session_token,
            max_age=services.config.admin_session_hours * 3600,
            httponly=True,
            secure=services.config.environment == "production",
            samesite="lax",
        )
        return response

    @app.post("/log/logout")
    async def log_logout(request: Request):
        response = RedirectResponse(url="/log?notice=Signed+out", status_code=303)
        response.delete_cookie(ADMIN_COOKIE_NAME)
        return response

    @app.post("/log/clear")
    async def clear_log_file(request: Request):
        _require_admin(request)
        services.config.log_path.parent.mkdir(parents=True, exist_ok=True)
        services.config.log_path.write_text("", encoding="utf-8")
        return RedirectResponse(url="/log?notice=Logs+cleared", status_code=303)

    @app.post("/log/reset")
    async def reset_service(request: Request):
        _require_admin(request)
        result = services.processor.reset_service_state()
        notice = (
            f"Service state reset. Cleared {result['jobs_cleared']} jobs, "
            f"{result['objects_deleted']} objects, and {result['workspaces_cleared']} workspaces."
        )
        return RedirectResponse(url=f"/log?notice={quote_plus(notice)}", status_code=303)

    @app.post("/api/jobs/initiate")
    async def initiate_job(payload: InitiateJobRequest, request: Request):
        services_local = _services(request)
        config_local = services_local.config

        output_format = (payload.output_format or config_local.default_output_format).strip().lower()
        if output_format not in FORMAT_LABELS:
            raise HTTPException(status_code=400, detail="Unsupported output format.")
        if payload.file_size <= 0:
            raise HTTPException(status_code=400, detail="File size must be greater than zero.")
        if payload.file_size > config_local.upload_max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {human_readable_size(config_local.upload_max_bytes)} upload limit.",
            )
        if Path(payload.filename).suffix.upper() != ".CR3":
            raise HTTPException(status_code=400, detail="Only .CR3 files are accepted.")

        client_ip_hash = hash_client_ip(_client_ip(request), config_local.effective_ip_salt)
        one_hour_ago = int(time.time()) - 3600
        if services_local.store.has_active_job_for_ip(client_ip_hash):
            logger.info("Rejected new job from %s because an active job already exists", client_ip_hash[:12])
            raise HTTPException(status_code=429, detail="You already have an active job in progress.")
        if services_local.store.count_recent_jobs_for_ip(client_ip_hash, one_hour_ago) >= config_local.per_ip_hourly_limit:
            logger.info("Rejected new job from %s because the hourly limit was reached", client_ip_hash[:12])
            raise HTTPException(status_code=429, detail="Hourly job limit reached for this IP.")

        job_id = uuid.uuid4().hex
        created_at = int(time.time())
        expires_at = created_at + config_local.job_ttl_seconds
        source_key = build_source_key(config_local, job_id, payload.filename)
        job = services_local.store.create_job(
            job_id=job_id,
            client_ip_hash=client_ip_hash,
            original_filename=Path(payload.filename).name,
            file_size=payload.file_size,
            output_format=output_format,
            source_key=source_key,
            created_at=created_at,
            expires_at=expires_at,
        )
        upload = services_local.storage.create_upload_target(
            job.id,
            job.source_key,
            config_local.presigned_upload_expiration_seconds,
        )
        logger.info(
            "Created web job %s for %s (%s, %s)",
            job.id,
            job.original_filename,
            FORMAT_LABELS.get(job.output_format, job.output_format),
            human_readable_size(job.file_size),
        )
        return {
            "job_id": job.id,
            "output_format": job.output_format,
            "max_size": config_local.upload_max_bytes,
            "expires_at": job.expires_at,
            "upload": _serialize_upload_target(upload),
        }

    @app.put("/api/jobs/{job_id}/local-upload")
    async def local_upload(job_id: str, request: Request, expires: int, token: str):
        services_local = _services(request)
        if not isinstance(services_local.storage, LocalStorage):
            raise HTTPException(status_code=404, detail="Local upload endpoint is unavailable.")
        if not verify_signed_token(services_local.config.app_secret, "upload", job_id, token):
            raise HTTPException(status_code=403, detail="Upload token is invalid or expired.")

        job = services_local.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.status != "initiated":
            raise HTTPException(status_code=409, detail="This job no longer accepts uploads.")

        destination = services_local.storage.resolve_path(job.source_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > services_local.config.upload_max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Upload exceeds the configured file size limit.",
            )

        written = 0
        try:
            with destination.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > services_local.config.upload_max_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Upload exceeds the configured file size limit.",
                        )
                    handle.write(chunk)
        except Exception:
            if destination.exists():
                destination.unlink(missing_ok=True)
            raise

        if written <= 0:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded file was empty.")

        return JSONResponse({"ok": True, "expires": expires})

    @app.post("/api/jobs/{job_id}/upload-complete")
    async def upload_complete(job_id: str, request: Request):
        services_local = _services(request)
        job = services_local.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.status in {"uploaded", "processing", "completed"}:
            return _serialize_job(job)
        if job.status != "initiated":
            raise HTTPException(status_code=409, detail="This job cannot be marked uploaded.")
        if not services_local.storage.object_exists(job.source_key):
            raise HTTPException(status_code=400, detail="Upload is not visible in storage yet.")

        updated = services_local.store.mark_uploaded(job.id, int(time.time()))
        if updated is None:
            raise HTTPException(status_code=409, detail="Job upload state changed unexpectedly.")
        logger.info("Upload completed for job %s", job.id)
        return _serialize_job(updated)

    @app.get("/api/jobs/recover")
    async def recover_job(request: Request):
        services_local = _services(request)
        client_ip_hash = hash_client_ip(_client_ip(request), services_local.config.effective_ip_salt)
        job = services_local.store.get_recoverable_job_for_ip(client_ip_hash, int(time.time()))
        if job is None:
            raise HTTPException(status_code=404, detail="No recoverable job found for this browser.")
        logger.info("Recovered job %s for %s", job.id, client_ip_hash[:12])
        return _serialize_job(job)

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, request: Request):
        job = _services(request).store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found or already cleaned up.")
        return _serialize_job(job)

    @app.post("/api/jobs/{job_id}/download-link")
    async def get_download_link(job_id: str, request: Request):
        services_local = _services(request)
        job = services_local.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.status != "completed" or not job.result_key:
            raise HTTPException(status_code=409, detail="ZIP file is not ready yet.")

        shortened_expiry = min(
            job.expires_at,
            int(time.time()) + services_local.config.post_download_ttl_seconds,
        )
        services_local.store.record_download_request(job.id, expires_at=shortened_expiry)
        target = services_local.storage.create_download_target(
            job.id,
            job.result_key,
            build_archive_name(job.original_filename, job.output_format),
            services_local.config.presigned_download_expiration_seconds,
        )
        logger.info("Issued download link for job %s", job.id)
        return {
            "job_id": job.id,
            "expires_at": shortened_expiry,
            "download": _serialize_download_target(target),
        }

    @app.get("/api/jobs/{job_id}/local-download")
    async def local_download(job_id: str, request: Request, expires: int, token: str):
        services_local = _services(request)
        if not isinstance(services_local.storage, LocalStorage):
            raise HTTPException(status_code=404, detail="Local download endpoint is unavailable.")
        if not verify_signed_token(services_local.config.app_secret, "download", job_id, token):
            raise HTTPException(status_code=403, detail="Download token is invalid or expired.")

        job = services_local.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.status != "completed" or not job.result_key:
            raise HTTPException(status_code=409, detail="ZIP file is not ready.")

        file_path = services_local.storage.resolve_path(job.result_key)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="ZIP file was already cleaned up.")

        return FileResponse(
            file_path,
            media_type="application/zip",
            filename=build_archive_name(job.original_filename, job.output_format),
        )

    @app.get("/healthz")
    async def healthz(request: Request):
        services_local = _services(request)
        status_code = 200
        details: dict[str, object] = {
            "database": "ok",
            "storage": "ok",
            "dnglab": "unknown",
            "storage_backend": services_local.config.storage_backend,
        }

        try:
            services_local.store.healthcheck()
        except Exception as exc:
            status_code = 503
            details["database"] = str(exc)

        try:
            services_local.storage.healthcheck()
        except Exception as exc:
            status_code = 503
            details["storage"] = str(exc)

        dnglab_ok, dnglab_info = services_local.processor.dnglab_status()
        details["dnglab"] = dnglab_info
        if services_local.config.require_dnglab and not dnglab_ok:
            status_code = 503

        return JSONResponse(details, status_code=status_code)

    return app


def _services(request: Request) -> AppServices:
    return request.app.state.services


def _client_ip(request: Request) -> str:
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def _is_admin_authenticated(request: Request) -> bool:
    services = _services(request)
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        return False
    return verify_signed_token(
        services.config.app_secret,
        ADMIN_COOKIE_PURPOSE,
        hash_secret_value(services.config.effective_admin_token),
        token,
    )


def _require_admin(request: Request) -> None:
    if not _is_admin_authenticated(request):
        raise HTTPException(status_code=403, detail="Admin login required.")


def _read_log_tail(log_path: Path, max_lines: int) -> list[str]:
    if not log_path.exists():
        return []

    tail: deque[str] = deque(maxlen=max_lines)
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            tail.append(line.rstrip("\n"))
    return list(tail)


def _serialize_job(job: JobRecord) -> dict[str, object]:
    return {
        "job_id": job.id,
        "status": job.status,
        "output_format": job.output_format,
        "output_format_label": FORMAT_LABELS.get(job.output_format, job.output_format.upper()),
        "progress_pct": job.progress_pct,
        "progress_message": job.progress_message,
        "frame_count": job.frame_count,
        "error_message": job.error_message,
        "download_ready": job.status == "completed" and bool(job.result_key),
        "expires_at": job.expires_at,
        "download_count": job.download_count,
    }


def _serialize_upload_target(target: UploadTarget) -> dict[str, object]:
    return {
        "url": target.url,
        "method": target.method,
        "headers": target.headers,
    }


def _serialize_download_target(target: DownloadTarget) -> dict[str, object]:
    return {
        "url": target.url,
        "method": target.method,
        "headers": target.headers,
    }
