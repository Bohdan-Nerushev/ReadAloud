"""
Unit tests for manifest checkpointing and recovery state in PersistenceService.
"""

import unittest
import tempfile
import shutil
from pathlib import Path
from src.application.services.persistence_service import PersistenceService


class TestManifestCheckpointRecovery(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "state.json"
        self.persistence = PersistenceService(str(self.state_file))

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_save_and_load_manifest(self):
        """Verify saving and loading manifest.json using atomic semantics."""
        manifest_data = {
            "task_id": "12345",
            "total_chunks": 5,
            "completed_count": 3,
            "audio_files": ["1.mp3", "2.mp3", "3.mp3", None, None]
        }

        success = self.persistence.save_manifest(self.temp_dir, manifest_data)
        self.assertTrue(success)

        manifest_path = Path(self.temp_dir) / "manifest.json"
        self.assertTrue(manifest_path.exists())

        loaded = self.persistence.load_manifest(self.temp_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["task_id"], "12345")
        self.assertEqual(loaded["completed_count"], 3)
        self.assertEqual(len(loaded["audio_files"]), 5)

    def test_load_manifest_nonexistent(self):
        """Verify load_manifest returns None when manifest file does not exist."""
        loaded = self.persistence.load_manifest(self.temp_dir)
        self.assertIsNone(loaded)


if __name__ == "__main__":
    unittest.main()
