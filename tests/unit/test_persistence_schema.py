"""Regression tests for H4: versioned state.json with migration from the legacy list."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.unit._lifecycle_helpers import make_config

from src.application.services.persistence_service import (
    STATE_SCHEMA_VERSION,
    PersistenceService,
)
from src.domain.models import GenerationTask, TtsBackend


class TestStateSchema(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = self.tmp / "state.json"
        self.service = PersistenceService(str(self.state))

    def _task(self, backend, name="Book"):
        return GenerationTask(config=make_config(self.tmp, name, backend))

    def _raw(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def test_saved_file_is_versioned_envelope(self):
        self.service.save_state([self._task(TtsBackend.PIPER)])
        raw = self._raw()
        self.assertEqual(raw["version"], STATE_SCHEMA_VERSION)
        self.assertEqual(raw["tasks"][0]["config"]["tts_backend"], TtsBackend.PIPER.value)

    def test_roundtrip_keeps_every_backend(self):
        tasks = [self._task(b, f"B{i}") for i, b in enumerate(TtsBackend)]
        self.service.save_state(tasks)
        restored = PersistenceService(str(self.state)).load_state()
        self.assertEqual([t.config.tts_backend for t in restored], [t.config.tts_backend for t in tasks])

    def test_legacy_v1_list_is_migrated_and_rewritten_as_current(self):
        self.service.save_state([self._task(TtsBackend.XTTS)])
        legacy = self._raw()["tasks"]
        self.state.write_text(json.dumps(legacy), encoding="utf-8")

        (task,) = self.service.load_state()
        self.assertEqual(task.config.tts_backend, TtsBackend.XTTS)

        self.service.save_state([task])
        self.assertEqual(self._raw()["version"], STATE_SCHEMA_VERSION)

    def test_legacy_list_without_backend_defaults_to_edge(self):
        self.service.save_state([self._task(TtsBackend.PIPER)])
        legacy = self._raw()["tasks"]
        del legacy[0]["config"]["tts_backend"]
        self.state.write_text(json.dumps(legacy), encoding="utf-8")
        (task,) = self.service.load_state()
        self.assertEqual(task.config.tts_backend, TtsBackend.EDGE_TTS)

    def test_newer_schema_is_preserved_not_destroyed(self):
        self.state.write_text(json.dumps({"version": 99, "tasks": []}), encoding="utf-8")
        self.assertEqual(self.service.load_state(), [])
        self.assertFalse(self.state.exists())
        self.assertTrue((self.tmp / "state.json.v99.bak").exists())

    def test_invalid_version_is_treated_as_corrupt(self):
        self.state.write_text(json.dumps({"version": "x", "tasks": []}), encoding="utf-8")
        self.assertEqual(self.service.load_state(), [])
        self.assertFalse(self.state.exists())

    def test_envelope_without_task_list_is_corrupt(self):
        self.state.write_text(json.dumps({"version": 2}), encoding="utf-8")
        self.assertEqual(self.service.load_state(), [])
        self.assertFalse(self.state.exists())

    def test_non_json_is_corrupt(self):
        self.state.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.service.load_state(), [])

    def test_missing_file_gives_empty_queue(self):
        self.assertEqual(self.service.load_state(), [])


if __name__ == "__main__":
    unittest.main()
