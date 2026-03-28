"""Environment-driven configuration for the web service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class WebConfig:
    """Configuration values for the Ubuntu web service."""

    environment: str = "development"
    app_name: str = "CSI Tool Web"
    public_base_url: str = "http://localhost:8080"
    app_secret: str = "change-me"
    client_ip_salt: str = ""
    database_path: Path = Path("data/csi_web.db")
    work_root: Path = Path("data/work")
    local_storage_root: Path = Path("data/storage")
    storage_backend: str = "local"
    dnglab_path: str = "/usr/local/bin/dnglab"
    require_dnglab: bool = False
    default_output_format: str = "dng"
    upload_max_bytes: int = 2 * 1024 * 1024 * 1024
    per_ip_active_limit: int = 1
    per_ip_hourly_limit: int = 3
    processing_concurrency: int = 1
    job_ttl_seconds: int = 60 * 60
    post_download_ttl_seconds: int = 5 * 60
    cleanup_interval_seconds: int = 10 * 60
    worker_poll_seconds: int = 5
    presigned_upload_expiration_seconds: int = 15 * 60
    presigned_download_expiration_seconds: int = 15 * 60
    r2_endpoint_url: str = ""
    r2_region_name: str = "auto"
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_upload_prefix: str = "uploads"
    r2_result_prefix: str = "results"

    @classmethod
    def from_env(cls) -> "WebConfig":
        """Build configuration from environment variables."""
        config = cls(
            environment=os.getenv("CSI_WEB_ENV", "development"),
            app_name=os.getenv("CSI_WEB_APP_NAME", "CSI Tool Web"),
            public_base_url=os.getenv("CSI_WEB_PUBLIC_BASE_URL", "http://localhost:8080"),
            app_secret=os.getenv("CSI_WEB_APP_SECRET", "change-me"),
            client_ip_salt=os.getenv("CSI_WEB_CLIENT_IP_SALT", ""),
            database_path=Path(os.getenv("CSI_WEB_DATABASE_PATH", "data/csi_web.db")),
            work_root=Path(os.getenv("CSI_WEB_WORK_ROOT", "data/work")),
            local_storage_root=Path(os.getenv("CSI_WEB_LOCAL_STORAGE_ROOT", "data/storage")),
            storage_backend=os.getenv("CSI_WEB_STORAGE_BACKEND", "local"),
            dnglab_path=os.getenv("CSI_WEB_DNGLAB_PATH", "/usr/local/bin/dnglab"),
            require_dnglab=_env_bool("CSI_WEB_REQUIRE_DNGLAB", False),
            default_output_format=os.getenv("CSI_WEB_DEFAULT_OUTPUT_FORMAT", "dng"),
            upload_max_bytes=_env_int("CSI_WEB_UPLOAD_MAX_BYTES", 2 * 1024 * 1024 * 1024),
            per_ip_active_limit=_env_int("CSI_WEB_PER_IP_ACTIVE_LIMIT", 1),
            per_ip_hourly_limit=_env_int("CSI_WEB_PER_IP_HOURLY_LIMIT", 3),
            processing_concurrency=_env_int("CSI_WEB_PROCESSING_CONCURRENCY", 1),
            job_ttl_seconds=_env_int("CSI_WEB_JOB_TTL_SECONDS", 60 * 60),
            post_download_ttl_seconds=_env_int("CSI_WEB_POST_DOWNLOAD_TTL_SECONDS", 5 * 60),
            cleanup_interval_seconds=_env_int("CSI_WEB_CLEANUP_INTERVAL_SECONDS", 10 * 60),
            worker_poll_seconds=_env_int("CSI_WEB_WORKER_POLL_SECONDS", 5),
            presigned_upload_expiration_seconds=_env_int(
                "CSI_WEB_PRESIGNED_UPLOAD_EXPIRATION_SECONDS",
                15 * 60,
            ),
            presigned_download_expiration_seconds=_env_int(
                "CSI_WEB_PRESIGNED_DOWNLOAD_EXPIRATION_SECONDS",
                15 * 60,
            ),
            r2_endpoint_url=os.getenv("CSI_WEB_R2_ENDPOINT_URL", ""),
            r2_region_name=os.getenv("CSI_WEB_R2_REGION_NAME", "auto"),
            r2_access_key_id=os.getenv("CSI_WEB_R2_ACCESS_KEY_ID", ""),
            r2_secret_access_key=os.getenv("CSI_WEB_R2_SECRET_ACCESS_KEY", ""),
            r2_bucket_name=os.getenv("CSI_WEB_R2_BUCKET_NAME", ""),
            r2_upload_prefix=os.getenv("CSI_WEB_R2_UPLOAD_PREFIX", "uploads"),
            r2_result_prefix=os.getenv("CSI_WEB_R2_RESULT_PREFIX", "results"),
        )
        config.validate()
        return config

    @property
    def effective_ip_salt(self) -> str:
        """Return the salt used to hash IP addresses."""
        return self.client_ip_salt or self.app_secret

    def ensure_runtime_dirs(self) -> None:
        """Create local runtime directories."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.local_storage_root.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        """Validate supported configuration choices."""
        self.public_base_url = self.public_base_url.rstrip("/")
        self.storage_backend = self.storage_backend.strip().lower()
        self.default_output_format = self.default_output_format.strip().lower()
        self.r2_upload_prefix = self.r2_upload_prefix.strip("/").lower() or "uploads"
        self.r2_result_prefix = self.r2_result_prefix.strip("/").lower() or "results"

        if self.storage_backend not in {"local", "r2"}:
            raise ValueError("CSI_WEB_STORAGE_BACKEND must be 'local' or 'r2'")
        if self.default_output_format not in {"dng", "cr3"}:
            raise ValueError("CSI_WEB_DEFAULT_OUTPUT_FORMAT must be 'dng' or 'cr3'")
        if self.per_ip_active_limit < 1:
            raise ValueError("CSI_WEB_PER_IP_ACTIVE_LIMIT must be >= 1")
        if self.per_ip_hourly_limit < 1:
            raise ValueError("CSI_WEB_PER_IP_HOURLY_LIMIT must be >= 1")
        if self.upload_max_bytes < 1:
            raise ValueError("CSI_WEB_UPLOAD_MAX_BYTES must be >= 1")
        if self.processing_concurrency != 1:
            raise ValueError("CSI_WEB_PROCESSING_CONCURRENCY is fixed to 1 for v1")
        if self.storage_backend == "r2":
            missing = [
                name for name, value in (
                    ("CSI_WEB_R2_ENDPOINT_URL", self.r2_endpoint_url),
                    ("CSI_WEB_R2_ACCESS_KEY_ID", self.r2_access_key_id),
                    ("CSI_WEB_R2_SECRET_ACCESS_KEY", self.r2_secret_access_key),
                    ("CSI_WEB_R2_BUCKET_NAME", self.r2_bucket_name),
                ) if not value
            ]
            if missing:
                raise ValueError(f"Missing required R2 config: {', '.join(missing)}")
