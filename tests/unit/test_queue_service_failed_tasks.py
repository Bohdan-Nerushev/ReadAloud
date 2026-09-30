"""QueueService keeps FAILED/STOPPED tasks reachable (regression: failed task vanished, Retry was a no-op)."""

import json
import tempfile
import unittest
from pathlib import Path

from tests.unit._lifecycle_helpers import make_config
from src.application.services.persistence_service import PersistenceService
from src.application.services.queue_service import QueueService
from src.domain.models import TaskStatus, TtsBackend


class TestFailedTaskRegistry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.qs = QueueService()

    def tearDown(self):
        self._tmp.cleanup()

    def _start_and_fail(self, name="Book", status=TaskStatus.FAILED):
        task = self.qs.add_task(make_config(self.tmp, name))
        self.assertIs(self.qs.get_next_task(), task)
        self.qs.update_task_status(status, "boom")
        self.qs.finalize_current_task()
        return task

    def test_failed_task_is_kept_after_finalize(self):
        task = self._start_and_fail()
        self.assertIsNone(self.qs.get_current_task())
        self.assertEqual(self.qs.get_all_tasks(), [task])
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.message, "boom")

    def test_stopped_task_is_kept_after_finalize(self):
        task = self._start_and_fail(status=TaskStatus.STOPPED)
        self.assertEqual(self.qs.get_all_tasks(), [task])

    def test_completed_task_is_not_kept(self):
        self._start_and_fail(status=TaskStatus.COMPLETED)
        self.assertEqual(self.qs.get_all_tasks(), [])

    def test_unfinished_task_is_not_kept(self):
        self._start_and_fail(status=TaskStatus.PROCESSING)
        self.assertEqual(self.qs.get_all_tasks(), [])

    def test_finalize_twice_does_not_duplicate(self):
        task = self._start_and_fail()
        self.qs.finalize_current_task()
        self.assertEqual(self.qs.get_all_tasks(), [task])

    def test_failed_task_is_never_picked_as_next(self):
        self._start_and_fail("A")
        self.assertIsNone(self.qs.get_next_task())

    def test_retry_task_finds_failed_task(self):
        """The original bug: retry_task() returned None for a failed task."""
        task = self._start_and_fail()
        retried = self.qs.retry_task(str(task.id))
        self.assertIs(retried, task)
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertIn("Pending Retry", task.message)

    def test_retried_task_moves_from_history_to_front_of_queue(self):
        failed = self._start_and_fail("A")
        other = self.qs.add_task(make_config(self.tmp, "B"))
        self.qs.retry_task(str(failed.id))
        self.assertEqual(self.qs.get_all_tasks(), [failed, other])
        self.assertIs(self.qs.get_next_task(), failed)

    def test_retried_task_is_listed_exactly_once(self):
        task = self._start_and_fail()
        self.qs.retry_task(str(task.id))
        self.assertEqual(self.qs.get_all_tasks().count(task), 1)

    def test_retry_task_notifies_subscribers(self):
        task = self._start_and_fail()
        seen = []
        self.qs.subscribe_task_updated(seen.append)
        self.qs.retry_task(str(task.id))
        self.assertEqual(seen, [task])

    def test_retry_unknown_task_returns_none(self):
        self._start_and_fail()
        self.assertIsNone(self.qs.retry_task("does-not-exist"))

    def test_toggle_pause_on_failed_task_retries_it(self):
        task = self._start_and_fail()
        self.assertIs(self.qs.toggle_task_pause(str(task.id)), task)
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertIs(self.qs.get_next_task(), task)

    def test_remove_task_removes_from_history(self):
        task = self._start_and_fail()
        self.assertTrue(self.qs.remove_task(str(task.id)))
        self.assertEqual(self.qs.get_all_tasks(), [])
        self.assertFalse(self.qs.remove_task(str(task.id)))

    def test_clear_all_tasks_clears_history(self):
        self._start_and_fail("A")
        self.qs.add_task(make_config(self.tmp, "B"))
        self.qs.clear_all_tasks()
        self.assertEqual(self.qs.get_all_tasks(), [])

    def test_get_all_tasks_order_is_current_queue_history(self):
        failed = self._start_and_fail("A")
        current_src = self.qs.add_task(make_config(self.tmp, "B"))
        current = self.qs.get_next_task()
        queued = self.qs.add_task(make_config(self.tmp, "C"))
        self.assertIs(current, current_src)
        self.assertEqual(self.qs.get_all_tasks(), [current, queued, failed])

    def test_find_task_searches_everywhere(self):
        failed = self._start_and_fail("A")
        current = self.qs.add_task(make_config(self.tmp, "B"))
        self.qs.get_next_task()
        queued = self.qs.add_task(make_config(self.tmp, "C"))
        for task in (failed, current, queued):
            self.assertIs(self.qs.find_task(str(task.id)), task)
        self.assertIsNone(self.qs.find_task("nope"))

    def test_discard_current_task_does_not_keep_it(self):
        task = self.qs.add_task(make_config(self.tmp))
        self.qs.get_next_task()
        self.assertIs(self.qs.discard_current_task(), task)
        self.assertIsNone(self.qs.get_current_task())
        self.assertEqual(self.qs.get_all_tasks(), [])
        self.assertIsNone(self.qs.discard_current_task())

    def test_discarding_a_failed_current_task_does_not_keep_it(self):
        self.qs.add_task(make_config(self.tmp))
        self.qs.get_next_task()
        self.qs.update_task_status(TaskStatus.FAILED, "x")
        self.qs.discard_current_task()
        self.assertEqual(self.qs.get_all_tasks(), [])

    def test_restore_failed_task_registers_it_once(self):
        source = QueueService()
        task = source.add_task(make_config(self.tmp))
        task.update_status(TaskStatus.FAILED, "old failure")
        self.qs.restore_failed_task(task)
        self.qs.restore_failed_task(task)
        self.assertEqual(self.qs.get_all_tasks(), [task])
        self.assertIsNone(self.qs.get_next_task())


class TestPersistenceKeepsBackend(unittest.TestCase):
    """Regression: tts_backend was not persisted, so a Piper/XTTS task came back as Edge (cloud)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state.json"
        self.persistence = PersistenceService(str(self.state))

    def tearDown(self):
        self._tmp.cleanup()

    def _roundtrip(self, backend, status=TaskStatus.PENDING):
        qs = QueueService()
        task = qs.add_task(make_config(self.tmp, "Book", backend))
        task.update_status(status, "m")
        self.assertTrue(self.persistence.save_state([task]))
        return self.persistence.load_state()

    def test_backend_survives_roundtrip(self):
        for backend in TtsBackend:
            with self.subTest(backend=backend):
                (restored,) = self._roundtrip(backend)
                self.assertEqual(restored.config.tts_backend, backend)

    def test_failed_task_roundtrips_with_status_message_and_dirs(self):
        qs = QueueService()
        task = qs.add_task(make_config(self.tmp, "Book", TtsBackend.PIPER))
        task.update_status(TaskStatus.FAILED, "Piper exploded")
        task.text_dir, task.audio_dir = "/t/text", "/t/audio"
        self.persistence.save_state([task])

        (restored,) = self.persistence.load_state()
        self.assertEqual(restored.status, TaskStatus.FAILED)
        self.assertEqual(restored.message, "Piper exploded")
        self.assertEqual((restored.text_dir, restored.audio_dir), ("/t/text", "/t/audio"))
        self.assertEqual(restored.config.tts_backend, TtsBackend.PIPER)

    def test_state_written_before_backend_was_persisted_defaults_to_edge(self):
        self._roundtrip(TtsBackend.EDGE_TTS)
        data = json.loads(self.state.read_text(encoding="utf-8"))
        del data[0]["config"]["tts_backend"]
        self.state.write_text(json.dumps(data), encoding="utf-8")

        (restored,) = self.persistence.load_state()
        self.assertEqual(restored.config.tts_backend, TtsBackend.EDGE_TTS)

    def test_unknown_backend_value_skips_task_instead_of_guessing(self):
        self._roundtrip(TtsBackend.XTTS)
        data = json.loads(self.state.read_text(encoding="utf-8"))
        data[0]["config"]["tts_backend"] = "martian"
        self.state.write_text(json.dumps(data), encoding="utf-8")

        self.assertEqual(self.persistence.load_state(), [])


if __name__ == "__main__":
    unittest.main()
