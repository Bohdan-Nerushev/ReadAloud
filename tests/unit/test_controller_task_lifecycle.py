"""
ApplicationController task lifecycle with real queue/persistence/file services.

Regressions covered:
  * a failed task vanished from the backend, so Retry did nothing, it was not persisted
    and its temporary directories leaked;
  * cancelling the running task wiped the whole queue.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.unit._lifecycle_helpers import make_config, make_controller, make_temp_dirs
from src.domain.models import TaskStatus, TtsBackend


class LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state.json"
        (self.controller, self.queue, self.persistence,
         self.generation, self.assembly) = make_controller(self.state)
        # Preparation spawns a QThread; the lifecycle under test does not need it.
        patcher = patch.object(self.controller, "_start_preparation_worker")
        self.prep = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, name, backend=TtsBackend.EDGE_TTS):
        self.controller.add_task(make_config(self.tmp, name, backend))
        return self.queue.find_task(self._id_of(name))

    def _id_of(self, name):
        for task in self.queue.get_all_tasks():
            if task.config.project_name == name:
                return str(task.id)
        raise AssertionError(f"no task named {name}")

    def persisted(self):
        return self.persistence.load_state()

    def attach_dirs(self, task):
        task.text_dir, task.audio_dir = make_temp_dirs(self.tmp, task.config.project_name)
        return Path(task.text_dir), Path(task.audio_dir)


class TestFailedTaskStaysAvailable(LifecycleTestCase):
    def test_failed_task_is_still_listed_with_failed_status(self):
        task = self.add("A")
        self.controller._handle_task_failure("synthesis exploded")

        self.assertIsNone(self.controller.get_current_task())
        self.assertIn(task, self.queue.get_all_tasks())
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.message, "synthesis exploded")

    def test_retry_button_restarts_the_failed_task(self):
        """The original bug: retry_generation() found nothing and silently did nothing."""
        task = self.add("A")
        self.controller._handle_task_failure("boom")
        self.prep.reset_mock()

        self.controller.retry_generation(str(task.id))

        self.assertIs(self.controller.get_current_task(), task)
        self.assertEqual(task.status, TaskStatus.PROCESSING)
        self.prep.assert_called_once()

    def test_retry_twice_in_a_row_after_two_failures(self):
        task = self.add("A")
        for _ in range(2):
            self.controller._handle_task_failure("boom")
            self.controller.retry_generation(str(task.id))
            self.assertIs(self.controller.get_current_task(), task)
        self.assertEqual(self.queue.get_all_tasks().count(task), 1)

    def test_failure_moves_on_to_the_next_task(self):
        first, second = self.add("A"), self.add("B")
        self.controller._handle_task_failure("boom")

        self.assertIs(self.controller.get_current_task(), second)
        self.assertEqual(second.status, TaskStatus.PROCESSING)
        self.assertEqual(first.status, TaskStatus.FAILED)
        self.assertEqual(self.queue.get_all_tasks(), [second, first])

    def test_retry_while_another_task_runs_queues_the_retry_first(self):
        first, second, third = self.add("A"), self.add("B"), self.add("C")
        self.controller._handle_task_failure("boom")  # A fails, B runs
        self.controller.retry_generation(str(first.id))

        self.assertIs(self.controller.get_current_task(), second)
        self.assertEqual(first.status, TaskStatus.PENDING)
        self.controller._handle_task_failure("boom again")  # B fails -> retried A goes before C
        self.assertIs(self.controller.get_current_task(), first)

    def test_failure_of_one_task_does_not_fail_the_rest(self):
        self.add("A")
        self.add("B")
        third = self.add("C")
        self.controller._handle_task_failure("boom")
        self.assertEqual(third.status, TaskStatus.PENDING)

    def test_pause_button_on_failed_task_resumes_it(self):
        task = self.add("A")
        self.controller._handle_task_failure("boom")
        self.controller.pause_generation(str(task.id))
        self.assertIs(self.controller.get_current_task(), task)

    def test_failure_stops_generation_so_stale_workers_cannot_feed_next_task(self):
        self.add("A")
        self.generation.stop.reset_mock()
        self.controller._handle_task_failure("boom")
        self.generation.stop.assert_called()
        self.assertTrue(self.controller._is_stopped or self.controller.get_current_task() is None)

    def test_failure_emits_error_once(self):
        self.add("A")
        errors = []
        self.controller.errorOccurred.connect(errors.append)
        self.controller._handle_task_failure("boom")
        self.assertEqual(errors, ["boom"])

    def test_failure_without_current_task_does_not_crash_or_invent_tasks(self):
        self.controller._handle_task_failure("boom")
        self.assertEqual(self.queue.get_all_tasks(), [])


class TestFailedTaskPersistence(LifecycleTestCase):
    def test_failed_task_is_written_to_state_file(self):
        task = self.add("A")
        self.controller._handle_task_failure("boom")

        (saved,) = self.persisted()
        self.assertEqual(saved.id, task.id)
        self.assertEqual(saved.status, TaskStatus.FAILED)
        self.assertEqual(saved.message, "boom")

    def test_restart_restores_failed_task_as_failed_not_pending(self):
        """A failed task must not silently restart on its own after the application restarts."""
        self.add("A")
        self.controller._handle_task_failure("boom")

        controller2, queue2, *_ = make_controller(self.state)
        controller2.restore_state()

        (restored,) = queue2.get_all_tasks()
        self.assertEqual(restored.status, TaskStatus.FAILED)
        self.assertIsNone(queue2.get_current_task())
        self.assertIsNone(queue2.get_next_task())

    def test_restored_failed_task_can_be_retried(self):
        self.add("A")
        self.controller._handle_task_failure("boom")

        controller2, queue2, *_ = make_controller(self.state)
        controller2.restore_state()
        (restored,) = queue2.get_all_tasks()
        with patch.object(controller2, "_start_preparation_worker"):
            controller2.retry_generation(str(restored.id))

        self.assertIs(controller2.get_current_task(), restored)
        self.assertEqual(restored.status, TaskStatus.PROCESSING)

    def test_restart_keeps_backend_of_failed_piper_task(self):
        self.add("A", TtsBackend.PIPER)
        self.controller._handle_task_failure("boom")

        controller2, queue2, *_ = make_controller(self.state)
        controller2.restore_state()
        (restored,) = queue2.get_all_tasks()
        self.assertEqual(restored.config.tts_backend, TtsBackend.PIPER)

    def test_restore_mixes_pending_and_failed_tasks_correctly(self):
        self.add("A")
        self.add("B")
        self.add("C")
        self.controller._handle_task_failure("boom")  # A failed, B running, C waiting
        self.controller._save_state(force=True)

        controller2, queue2, *_ = make_controller(self.state)
        controller2.restore_state()

        by_name = {t.config.project_name: t for t in queue2.get_all_tasks()}
        self.assertEqual(set(by_name), {"A", "B", "C"})
        self.assertEqual(by_name["A"].status, TaskStatus.FAILED)
        self.assertEqual(by_name["B"].status, TaskStatus.PAUSED)
        self.assertEqual(by_name["C"].status, TaskStatus.PENDING)

    def test_restore_announces_failed_tasks_to_the_ui(self):
        self.add("A")
        self.controller._handle_task_failure("boom")

        controller2, *_ = make_controller(self.state)
        announced = []
        controller2.taskAdded.connect(announced.append)
        controller2.restore_state()
        self.assertEqual([t.status for t in announced], [TaskStatus.FAILED])

    def test_deleted_failed_task_is_not_persisted_anymore(self):
        task = self.add("A")
        self.controller._handle_task_failure("boom")
        self.controller.cancel_task(str(task.id))
        self.assertEqual(self.persisted(), [])


class TestFailedTaskTempFiles(LifecycleTestCase):
    def test_failure_keeps_temp_dirs_so_retry_can_resume(self):
        task = self.add("A")
        text_dir, audio_dir = self.attach_dirs(task)
        self.controller._handle_task_failure("boom")
        self.assertTrue(text_dir.exists() and audio_dir.exists())

    def test_deleting_failed_task_removes_its_temp_dirs(self):
        task = self.add("A")
        text_dir, audio_dir = self.attach_dirs(task)
        self.controller._handle_task_failure("boom")

        self.controller.cancel_task(str(task.id))

        self.assertFalse(text_dir.exists())
        self.assertFalse(audio_dir.exists())
        self.assertEqual(self.queue.get_all_tasks(), [])

    def test_deleting_failed_task_does_not_touch_other_tasks_dirs(self):
        failed, other = self.add("A"), self.add("B")
        self.attach_dirs(failed)
        other_text, other_audio = self.attach_dirs(other)
        self.controller._handle_task_failure("boom")

        self.controller.cancel_task(str(failed.id))

        self.assertTrue(other_text.exists() and other_audio.exists())

    def test_deleting_task_without_temp_dirs_is_safe(self):
        task = self.add("A")
        self.controller._handle_task_failure("boom")
        self.controller.cancel_task(str(task.id))
        self.assertEqual(self.queue.get_all_tasks(), [])

    def test_deleting_failed_task_survives_already_missing_dirs(self):
        task = self.add("A")
        text_dir, audio_dir = self.attach_dirs(task)
        self.controller._handle_task_failure("boom")
        import shutil
        shutil.rmtree(text_dir)
        shutil.rmtree(audio_dir)
        self.controller.cancel_task(str(task.id))
        self.assertEqual(self.queue.get_all_tasks(), [])

    def test_cleanup_error_does_not_break_deletion(self):
        task = self.add("A")
        self.attach_dirs(task)
        self.controller._handle_task_failure("boom")
        with patch.object(self.controller._file_manager, "cleanup_temp_directories",
                          side_effect=OSError("denied")):
            self.controller.cancel_task(str(task.id))
        self.assertEqual(self.queue.get_all_tasks(), [])

    def test_retried_task_keeps_its_dirs(self):
        task = self.add("A")
        text_dir, audio_dir = self.attach_dirs(task)
        self.controller._handle_task_failure("boom")
        with patch.object(self.controller, "_start_task", return_value=True):
            self.controller.retry_generation(str(task.id))
        self.assertTrue(text_dir.exists() and audio_dir.exists())


class TestCancelCurrentTask(LifecycleTestCase):
    """cancel_task(current) used to call stop_generation() -> clear_all_tasks()."""

    def test_cancelling_running_task_keeps_the_rest_of_the_queue(self):
        first, second, third = self.add("A"), self.add("B"), self.add("C")

        self.controller.cancel_task(str(first.id))

        remaining = self.queue.get_all_tasks()
        self.assertEqual(remaining, [second, third])

    def test_next_task_starts_after_cancellation(self):
        first, second, third = self.add("A"), self.add("B"), self.add("C")
        self.prep.reset_mock()

        self.controller.cancel_task(str(first.id))

        self.assertIs(self.controller.get_current_task(), second)
        self.assertEqual(second.status, TaskStatus.PROCESSING)
        self.assertEqual(third.status, TaskStatus.PENDING)
        self.prep.assert_called_once()

    def test_cancelling_the_only_task_leaves_an_empty_idle_queue(self):
        task = self.add("A")
        self.controller.cancel_task(str(task.id))
        self.assertEqual(self.queue.get_all_tasks(), [])
        self.assertIsNone(self.controller.get_current_task())

    def test_cancelled_task_is_not_kept_for_retry(self):
        task = self.add("A")
        self.controller.cancel_task(str(task.id))
        self.assertIsNone(self.queue.retry_task(str(task.id)))
        self.assertEqual(self.persisted(), [])

    def test_cancelling_keeps_previously_failed_tasks(self):
        failed, running = self.add("A"), self.add("B")
        self.controller._handle_task_failure("boom")  # A failed, B running
        self.controller.cancel_task(str(running.id))
        self.assertEqual(self.queue.get_all_tasks(), [failed])

    def test_cancelling_removes_temp_dirs_of_the_cancelled_task_only(self):
        first, second = self.add("A"), self.add("B")
        first_dirs = self.attach_dirs(first)
        second_dirs = self.attach_dirs(second)

        self.controller.cancel_task(str(first.id))

        self.assertFalse(any(d.exists() for d in first_dirs))
        self.assertTrue(all(d.exists() for d in second_dirs))

    def test_cancelling_stops_generation_and_assembly(self):
        task = self.add("A")
        self.generation.stop.reset_mock()
        self.controller.cancel_task(str(task.id))
        self.generation.stop.assert_called_with()
        # the GUI thread must never block waiting for a running batch
        self.assertNotIn(True, [c.kwargs.get("wait") for c in self.generation.stop.call_args_list])
        self.assembly.stop.assert_called()

    def test_persisted_state_contains_only_surviving_tasks(self):
        first, second, third = self.add("A"), self.add("B"), self.add("C")
        self.controller.cancel_task(str(first.id))
        names = sorted(t.config.project_name for t in self.persisted())
        self.assertEqual(names, ["B", "C"])

    def test_controller_can_start_a_new_task_after_cancelling(self):
        task = self.add("A")
        self.controller.cancel_task(str(task.id))
        fresh = self.add("B")
        self.assertIs(self.controller.get_current_task(), fresh)
        self.assertFalse(self.controller._is_stopped)

    def test_cancel_current_task_is_public_and_equivalent(self):
        first, second = self.add("A"), self.add("B")
        self.controller.cancel_current_task()
        self.assertEqual(self.queue.get_all_tasks(), [second])

    def test_cancelling_a_queued_task_removes_only_that_task(self):
        first, second, third = self.add("A"), self.add("B"), self.add("C")
        self.controller.cancel_task(str(second.id))
        self.assertEqual(self.queue.get_all_tasks(), [first, third])
        self.assertIs(self.controller.get_current_task(), first)

    def test_cancelling_unknown_task_is_a_noop(self):
        first = self.add("A")
        self.controller.cancel_task("does-not-exist")
        self.assertEqual(self.queue.get_all_tasks(), [first])


class TestClearQueueStaysExplicit(LifecycleTestCase):
    def test_stop_generation_still_clears_everything(self):
        self.add("A")
        self.add("B")
        failed = self.add("C")
        self.controller._handle_task_failure("boom")  # A failed, B running
        self.controller.stop_generation()
        self.assertEqual(self.queue.get_all_tasks(), [])
        self.assertIsNone(failed and self.controller.get_current_task())

    def test_stop_generation_removes_temp_dirs_of_all_tasks_including_failed(self):
        failed, running = self.add("A"), self.add("B")
        failed_dirs = self.attach_dirs(failed)
        self.controller._handle_task_failure("boom")
        running_dirs = self.attach_dirs(running)

        self.controller.stop_generation()

        self.assertFalse(any(d.exists() for d in failed_dirs + running_dirs))


if __name__ == "__main__":
    unittest.main()
