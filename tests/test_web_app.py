"""Integration-style tests for the FastAPI web app."""

from __future__ import annotations

import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from csi_tool.core.models import BurstFile
from csi_tool.web.config import WebConfig

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None

if FASTAPI_AVAILABLE:
    from fastapi.testclient import TestClient
    from csi_tool.web.app import create_app


@unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI test dependencies are not installed.")
class WebAppIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        config = WebConfig(
            environment="test",
            app_name="CSI Tool Test",
            public_base_url="http://testserver",
            app_secret="test-secret",
            client_ip_salt="test-salt",
            database_path=root / "jobs.sqlite3",
            work_root=root / "work",
            local_storage_root=root / "storage",
            storage_backend="local",
            require_dnglab=False,
            default_output_format="dng",
        )
        self.app = create_app(config)
        self.client = TestClient(self.app)
        self.services = self.app.state.services

        def fake_parse_burst(input_path: Path) -> BurstFile:
            return BurstFile(
                path=input_path,
                filename=input_path.name,
                file_size=input_path.stat().st_size,
                frame_count=2,
            )

        def fake_extract_frames(job, burst, input_path, output_dir):
            del input_path
            output_dir.mkdir(parents=True, exist_ok=True)
            suffix = "dng" if job.output_format == "dng" else "cr3"
            extracted = []
            for index in range(burst.frame_count):
                frame_path = output_dir / f"frame_{index + 1:04d}.{suffix}"
                frame_path.write_bytes(f"frame-{index}".encode("ascii"))
                extracted.append(frame_path)
            return extracted

        self.parse_patch = patch.object(self.services.processor, "_parse_burst", side_effect=fake_parse_burst)
        self.extract_patch = patch.object(self.services.processor, "_extract_frames", side_effect=fake_extract_frames)
        self.parse_patch.start()
        self.extract_patch.start()

    def tearDown(self) -> None:
        self.extract_patch.stop()
        self.parse_patch.stop()
        self.client.close()
        self.temp_dir.cleanup()

    def test_full_local_job_flow(self) -> None:
        initiated = self.client.post(
            "/api/jobs/initiate",
            json={
                "filename": "burst.CR3",
                "file_size": 4096,
                "output_format": "dng",
            },
        )
        self.assertEqual(initiated.status_code, 200)
        initiated_payload = initiated.json()

        upload_response = self.client.put(
            initiated_payload["upload"]["url"],
            content=b"fake-cr3-payload",
            headers={"content-type": "application/octet-stream"},
        )
        self.assertEqual(upload_response.status_code, 200)

        job_id = initiated_payload["job_id"]
        complete = self.client.post(f"/api/jobs/{job_id}/upload-complete", json={})
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()["status"], "uploaded")

        processed = self.services.processor.process_next_job()
        self.assertTrue(processed)

        job_status = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(job_status.status_code, 200)
        job_payload = job_status.json()
        self.assertEqual(job_payload["status"], "completed")
        self.assertEqual(job_payload["frame_count"], 2)
        self.assertTrue(job_payload["download_ready"])

        download_meta = self.client.post(f"/api/jobs/{job_id}/download-link", json={})
        self.assertEqual(download_meta.status_code, 200)
        zip_response = self.client.get(download_meta.json()["download"]["url"])
        self.assertEqual(zip_response.status_code, 200)
        self.assertTrue(zip_response.content.startswith(b"PK"))

        self.services.store.record_download_request(job_id, expires_at=int(time.time()) - 1)
        cleaned = self.services.processor.cleanup_expired_jobs()
        self.assertEqual(cleaned, 1)
        self.assertIsNone(self.services.store.get_job(job_id))

    def test_rejects_wrong_extension(self) -> None:
        response = self.client.post(
            "/api/jobs/initiate",
            json={
                "filename": "burst.txt",
                "file_size": 128,
                "output_format": "dng",
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_oversized_payload(self) -> None:
        response = self.client.post(
            "/api/jobs/initiate",
            json={
                "filename": "burst.CR3",
                "file_size": self.services.config.upload_max_bytes + 1,
                "output_format": "dng",
            },
        )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
