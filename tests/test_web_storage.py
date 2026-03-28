"""Tests for the local storage backend."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csi_tool.web.storage import LocalStorage


class LocalStorageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = LocalStorage(Path(self.temp_dir.name), app_secret="test-secret")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_local_storage_round_trip(self) -> None:
        source_path = Path(self.temp_dir.name) / "source.bin"
        source_path.write_bytes(b"burst-data")

        self.storage.upload_file(source_path, "uploads/job-1/source.CR3", "application/octet-stream")
        self.assertTrue(self.storage.object_exists("uploads/job-1/source.CR3"))

        destination = Path(self.temp_dir.name) / "copy.bin"
        self.storage.download_file("uploads/job-1/source.CR3", destination)
        self.assertEqual(destination.read_bytes(), b"burst-data")

        upload_target = self.storage.create_upload_target("job-1", "uploads/job-1/source.CR3", 300)
        download_target = self.storage.create_download_target(
            "job-1",
            "results/job-1/source_dng_frames.zip",
            "source_dng_frames.zip",
            300,
        )
        self.assertIn("/api/jobs/job-1/local-upload", upload_target.url)
        self.assertIn("/api/jobs/job-1/local-download", download_target.url)

        self.storage.delete_objects(["uploads/job-1/source.CR3"])
        self.assertFalse(self.storage.object_exists("uploads/job-1/source.CR3"))


if __name__ == "__main__":
    unittest.main()
