"""Storage backends for uploads and generated ZIP files."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .auth import create_signed_token

try:
    import boto3
    from botocore.client import Config as BotoConfig
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover - exercised in environments without web deps
    boto3 = None
    BotoConfig = None
    ClientError = Exception


@dataclass(slots=True)
class UploadTarget:
    """How the browser should upload a source CR3."""

    url: str
    method: str = "PUT"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DownloadTarget:
    """How the browser should download a finished ZIP."""

    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)


class StorageBackend(ABC):
    """Abstract storage backend used by the web app and worker."""

    @abstractmethod
    def create_upload_target(self, job_id: str, object_key: str, expires_in: int) -> UploadTarget:
        """Return an upload target for a source CR3 file."""

    @abstractmethod
    def object_exists(self, object_key: str) -> bool:
        """Return True when an object already exists."""

    @abstractmethod
    def download_file(self, object_key: str, destination: Path) -> None:
        """Download an object to a local destination."""

    @abstractmethod
    def upload_file(self, source_path: Path, object_key: str, content_type: str) -> None:
        """Upload a local file into storage."""

    @abstractmethod
    def create_download_target(
        self,
        job_id: str,
        object_key: str,
        download_name: str,
        expires_in: int,
    ) -> DownloadTarget:
        """Return a download target for the finished ZIP."""

    @abstractmethod
    def delete_objects(self, object_keys: Iterable[str]) -> None:
        """Delete one or more objects from storage."""

    @abstractmethod
    def healthcheck(self) -> None:
        """Raise an exception if the backend is unhealthy."""


class LocalStorage(StorageBackend):
    """Filesystem-backed storage for local development and tests."""

    def __init__(self, root: Path, *, app_secret: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.app_secret = app_secret

    def create_upload_target(self, job_id: str, object_key: str, expires_in: int) -> UploadTarget:
        expires_at = self._future_expiry(expires_in)
        token = create_signed_token(self.app_secret, "upload", job_id, expires_at)
        return UploadTarget(
            url=f"/api/jobs/{job_id}/local-upload?expires={expires_at}&token={token}",
            headers={"Content-Type": "application/octet-stream"},
        )

    def object_exists(self, object_key: str) -> bool:
        return self.resolve_path(object_key).exists()

    def download_file(self, object_key: str, destination: Path) -> None:
        source = self.resolve_path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    def upload_file(self, source_path: Path, object_key: str, content_type: str) -> None:
        del content_type
        destination = self.resolve_path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(source_path).read_bytes())

    def create_download_target(
        self,
        job_id: str,
        object_key: str,
        download_name: str,
        expires_in: int,
    ) -> DownloadTarget:
        del object_key, download_name
        expires_at = self._future_expiry(expires_in)
        token = create_signed_token(self.app_secret, "download", job_id, expires_at)
        return DownloadTarget(url=f"/api/jobs/{job_id}/local-download?expires={expires_at}&token={token}")

    def delete_objects(self, object_keys: Iterable[str]) -> None:
        for key in object_keys:
            if not key:
                continue
            path = self.resolve_path(key)
            if path.exists():
                path.unlink()

    def healthcheck(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, object_key: str) -> Path:
        candidate = (self.root / object_key).resolve()
        root = self.root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("Object key resolved outside the local storage root")
        return candidate

    def write_upload_chunks(self, object_key: str, chunks: Iterable[bytes]) -> None:
        destination = self.resolve_path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            for chunk in chunks:
                if chunk:
                    handle.write(chunk)

    @staticmethod
    def _future_expiry(expires_in: int) -> int:
        import time

        return int(time.time()) + max(1, expires_in)


class R2Storage(StorageBackend):
    """Cloudflare R2 storage using the S3-compatible API."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        region_name: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
    ):
        if boto3 is None or BotoConfig is None:
            raise RuntimeError("boto3 is required to use the R2 storage backend.")
        self.bucket_name = bucket_name
        session = boto3.session.Session()
        self.client = session.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=BotoConfig(signature_version="s3v4"),
        )

    def create_upload_target(self, job_id: str, object_key: str, expires_in: int) -> UploadTarget:
        del job_id
        url = self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": object_key,
                "ContentType": "application/octet-stream",
            },
            ExpiresIn=expires_in,
        )
        return UploadTarget(url=url, headers={"Content-Type": "application/octet-stream"})

    def object_exists(self, object_key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=object_key)
            return True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def download_file(self, object_key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            self.client.download_fileobj(self.bucket_name, object_key, handle)

    def upload_file(self, source_path: Path, object_key: str, content_type: str) -> None:
        extra_args = {"ContentType": content_type} if content_type else None
        self.client.upload_file(str(source_path), self.bucket_name, object_key, ExtraArgs=extra_args or {})

    def create_download_target(
        self,
        job_id: str,
        object_key: str,
        download_name: str,
        expires_in: int,
    ) -> DownloadTarget:
        del job_id
        url = self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": object_key,
                "ResponseContentDisposition": f'attachment; filename="{download_name}"',
            },
            ExpiresIn=expires_in,
        )
        return DownloadTarget(url=url)

    def delete_objects(self, object_keys: Iterable[str]) -> None:
        keys = [{"Key": key} for key in object_keys if key]
        if not keys:
            return
        for index in range(0, len(keys), 1000):
            self.client.delete_objects(
                Bucket=self.bucket_name,
                Delete={"Objects": keys[index:index + 1000], "Quiet": True},
            )

    def healthcheck(self) -> None:
        self.client.head_bucket(Bucket=self.bucket_name)
