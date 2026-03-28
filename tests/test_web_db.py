"""Tests for the SQLite job store."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from csi_tool.web.db import JobStore


class JobStoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.sqlite3"
        self.store = JobStore(self.db_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_job_lifecycle_and_rate_limit_queries(self) -> None:
        now_ts = int(time.time())
        job = self.store.create_job(
            job_id="job-1",
            client_ip_hash="ip-hash",
            original_filename="sample.CR3",
            file_size=1024,
            output_format="dng",
            source_key="uploads/job-1/sample.CR3",
            created_at=now_ts,
            expires_at=now_ts + 3600,
        )

        self.assertEqual(job.status, "initiated")
        self.assertTrue(self.store.has_active_job_for_ip("ip-hash"))
        self.assertEqual(self.store.count_recent_jobs_for_ip("ip-hash", now_ts - 60), 1)

        uploaded = self.store.mark_uploaded("job-1", now_ts + 1)
        self.assertIsNotNone(uploaded)
        self.assertEqual(uploaded.status, "uploaded")

        claimed = self.store.claim_next_uploaded_job(now_ts + 2)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, "processing")

        self.store.update_progress("job-1", 55, "Halfway there", frame_count=12)
        updated = self.store.get_job("job-1")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.progress_pct, 55)
        self.assertEqual(updated.frame_count, 12)

        self.store.mark_failed("job-1", error_message="Broken burst", completed_at=now_ts + 3)
        failed = self.store.get_job("job-1")
        self.assertIsNotNone(failed)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_message, "Broken burst")

    def test_download_request_shortens_expiry(self) -> None:
        now_ts = int(time.time())
        self.store.create_job(
            job_id="job-2",
            client_ip_hash="ip-hash",
            original_filename="sample.CR3",
            file_size=2048,
            output_format="cr3",
            source_key="uploads/job-2/sample.CR3",
            created_at=now_ts,
            expires_at=now_ts + 3600,
        )
        self.store.mark_completed(
            "job-2",
            result_key="results/job-2/sample_cr3_frames.zip",
            frame_count=8,
            completed_at=now_ts + 30,
            expires_at=now_ts + 3600,
        )

        updated = self.store.record_download_request("job-2", expires_at=now_ts + 120)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.download_count, 1)
        self.assertEqual(updated.expires_at, now_ts + 120)


if __name__ == "__main__":
    unittest.main()
