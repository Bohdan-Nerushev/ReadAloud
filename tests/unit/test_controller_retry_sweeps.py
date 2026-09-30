"""
Retry-sweep behaviour of ApplicationController._check_completion with a real GenerationService.

Regression: execute_sweep reset ``_retry_in_progress`` right after *submitting* the chunks and
the progress tracker still said "everything processed", so the monitor launched a new sweep every
few seconds, exhausted all 5 sweeps while synthesis was still running, resynthesized the same
chunks concurrently (same ``N.mp3.tmp``) and then failed the task.
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QCoreApplication

from tests.unit._lifecycle_helpers import make_config, make_controller
from src.application.services.generation_service import GenerationService
from src.domain.base_generator import BaseAudioGenerator
from src.domain.models import AudioChunk, TaskStatus

INF = 10 ** 6


class FakeGenerator(BaseAudioGenerator):
    """Synthesizes instantly unless told to fail or to wait on a gate."""

    def __init__(self, fail_attempts=None):
        self._init_cancellation()
        self.fail_attempts = dict(fail_attempts or {})
        self.gate = None
        self.gate_from_attempt = 2
        self.attempts = {}
        self.calls = []
        self._active = {}
        self.max_concurrent_per_chunk = 0
        self._lock = threading.Lock()

    def generate_audio_batch(self, batch, language, gender, output_dir, chunk_callback=None,
                             max_workers=1, max_retries=10):
        with self._lock:
            self.calls.append([c.chunk_number for c in batch])
        results = []
        for chunk in batch:
            n = chunk.chunk_number
            with self._lock:
                self.attempts[n] = self.attempts.get(n, 0) + 1
                attempt = self.attempts[n]
                self._active[n] = self._active.get(n, 0) + 1
                self.max_concurrent_per_chunk = max(self.max_concurrent_per_chunk, self._active[n])
            try:
                if self.gate is not None and attempt >= self.gate_from_attempt:
                    self.gate.wait(10)
                if attempt <= self.fail_attempts.get(n, 0):
                    results.append(None)
                    continue
                path = Path(output_dir) / f"{n}.mp3"
                path.write_bytes(b"mp3-data")
                if chunk_callback:
                    chunk_callback(n, str(path), 1.0)
                results.append((str(path), 1.0))
            finally:
                with self._lock:
                    self._active[n] -= 1
        return results


def pump(condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        QCoreApplication.processEvents()
        if condition():
            QCoreApplication.processEvents()
            return True
        time.sleep(0.005)
    QCoreApplication.processEvents()
    return condition()


class SweepTestCase(unittest.TestCase):
    CHUNKS = 3

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.audio_dir = self.tmp / "audio"
        self.audio_dir.mkdir()
        self.generator = FakeGenerator()
        self.generation = GenerationService(self.generator)
        assembly = MagicMock()
        assembly.mark_chunk_ready.return_value = None
        assembly.mark_chunk_failed.return_value = None
        (self.controller, self.queue, _, _, self.assembly) = make_controller(
            self.tmp / "state.json", generation_service=self.generation, assembly_service=assembly)

        self.task = self.queue.add_task(make_config(self.tmp))
        self.queue.get_next_task()
        self.queue.update_task_status(TaskStatus.PROCESSING, "run")

        c = self.controller
        c._chunks = [AudioChunk(chunk_number=i, text_content=f"t{i}") for i in range(1, self.CHUNKS + 1)]
        c._audio_files = [None] * self.CHUNKS
        c._audio_dir = str(self.audio_dir)
        c._is_stopped = False
        c._finalize_generation = MagicMock()

        self.scheduled = []
        patcher = patch("src.application.app_controller.QTimer")
        self.qtimer = patcher.start()
        self.qtimer.singleShot.side_effect = lambda ms, cb: self.scheduled.append(cb)
        self.addCleanup(patcher.stop)
        self.addCleanup(self.generation.stop, True)
        self.addCleanup(self._tmp.cleanup)

    def run_initial_pass(self):
        self.generation.start_generation(self.task, self.controller._chunks, str(self.audio_dir))
        processed = lambda: self.generation.get_progress_info()[0] >= self.CHUNKS
        self.assertTrue(pump(processed), "initial pass did not finish")
        self.assertTrue(pump(lambda: sum(f is not None for f in self.controller._audio_files)
                             == self.CHUNKS - len(self.generator.fail_attempts)))

    def fire_scheduled_sweeps(self):
        pending, self.scheduled = self.scheduled, []
        for cb in pending:
            cb()
        return len(pending)

    def tick(self, times=1):
        for _ in range(times):
            self.controller._check_completion()


class TestSweepIsNotDuplicated(SweepTestCase):
    def test_single_sweep_is_scheduled_no_matter_how_often_the_monitor_ticks(self):
        self.generator.fail_attempts = {2: 1}
        self.run_initial_pass()

        self.tick(10)

        self.assertEqual(len(self.scheduled), 1)
        self.assertEqual(self.controller._retry_sweep_count, 0)

    def test_monitor_does_not_start_another_sweep_while_the_first_is_running(self):
        """The original bug: every tick after submission started yet another sweep."""
        self.generator.fail_attempts = {2: 1}
        self.generator.gate = threading.Event()
        self.run_initial_pass()

        self.tick()
        self.assertEqual(self.fire_scheduled_sweeps(), 1)
        self.assertEqual(self.controller._retry_sweep_count, 1)
        self.assertFalse(self.controller._retry_in_progress)

        self.tick(20)  # the chunk is still being synthesized

        self.assertEqual(self.scheduled, [])
        self.assertEqual(self.controller._retry_sweep_count, 1)
        self.controller._finalize_generation.assert_not_called()
        self.generator.gate.set()

    def test_sweep_progress_reflects_chunks_in_flight(self):
        self.generator.fail_attempts = {2: 1}
        self.generator.gate = threading.Event()
        self.run_initial_pass()
        self.assertEqual(self.generation.get_progress_info()[0], 3)

        self.tick()
        self.fire_scheduled_sweeps()

        processed, total, _, _ = self.generation.get_progress_info()
        self.assertEqual((processed, total), (2, 3))
        self.generator.gate.set()

    def test_successful_sweep_finalizes_exactly_once_with_a_single_retry(self):
        self.generator.fail_attempts = {2: 1}
        self.generator.gate = threading.Event()
        self.run_initial_pass()
        self.tick()
        self.fire_scheduled_sweeps()
        self.tick(5)

        self.generator.gate.set()
        self.assertTrue(pump(lambda: self.controller._audio_files[1] is not None))
        self.assertTrue(pump(lambda: self.generation.get_progress_info()[0] == 3))
        self.tick()

        self.controller._finalize_generation.assert_called_once()
        self.assertEqual(self.generator.attempts[2], 2)
        self.assertEqual(self.generator.calls, [[1, 2, 3], [2]])
        self.assertEqual(self.generation._progress_tracker.get_failed_count(), 0)
        self.assertEqual(self.generation._progress_tracker.get_completed_count(), 3)

    def test_nothing_missing_finalizes_without_any_sweep(self):
        self.run_initial_pass()
        self.tick()
        self.controller._finalize_generation.assert_called_once()
        self.assertEqual(self.scheduled, [])
        self.assertEqual(self.generator.calls, [[1, 2, 3]])

    def test_stopped_controller_never_schedules_a_sweep(self):
        self.generator.fail_attempts = {2: 1}
        self.run_initial_pass()
        self.controller._is_stopped = True
        self.tick(5)
        self.assertEqual(self.scheduled, [])
        self.controller._finalize_generation.assert_not_called()

    def test_monitor_does_nothing_before_generation_reports_anything(self):
        self.tick(3)
        self.assertEqual(self.scheduled, [])
        self.controller._finalize_generation.assert_not_called()


class TestSweepExecution(SweepTestCase):
    def test_missing_list_is_recomputed_when_the_sweep_runs(self):
        """Results reaching the GUI thread during the cooling pause must not cause a pointless sweep."""
        self.generator.fail_attempts = {2: 1}
        self.run_initial_pass()
        self.tick()  # schedules; chunk 2 still missing
        self.assertEqual(len(self.scheduled), 1)

        path = self.audio_dir / "2.mp3"
        path.write_bytes(b"late")
        self.controller._audio_files[1] = str(path)
        self.fire_scheduled_sweeps()

        self.assertEqual(self.controller._retry_sweep_count, 0)
        self.assertFalse(self.controller._retry_in_progress)
        self.assertEqual(self.generator.calls, [[1, 2, 3]])

    def test_sweep_is_dropped_when_the_task_changed_during_the_pause(self):
        self.generator.fail_attempts = {2: 1}
        self.run_initial_pass()
        self.tick()
        self.controller._retry_in_progress = True  # belongs to a newer task's sweep
        self.queue.discard_current_task()

        self.fire_scheduled_sweeps()

        self.assertEqual(self.generator.calls, [[1, 2, 3]])
        self.assertTrue(self.controller._retry_in_progress)

    def test_sweep_is_dropped_after_stop(self):
        self.generator.fail_attempts = {2: 1}
        self.run_initial_pass()
        self.tick()
        self.controller._is_stopped = True
        self.fire_scheduled_sweeps()
        self.assertEqual(self.generator.calls, [[1, 2, 3]])
        self.assertFalse(self.controller._retry_in_progress)

    def test_failure_to_submit_does_not_wedge_the_monitor(self):
        self.generator.fail_attempts = {2: 1}
        self.run_initial_pass()
        self.tick()
        with patch.object(self.generation, "retry_chunks", side_effect=RuntimeError("pool gone")):
            self.fire_scheduled_sweeps()
        self.assertFalse(self.controller._retry_in_progress)
        self.assertEqual(self.controller._retry_sweep_count, 1)

    def test_sweep_resubmits_only_the_missing_chunks(self):
        self.generator.fail_attempts = {1: 1, 3: 1}
        self.run_initial_pass()
        self.tick()
        self.fire_scheduled_sweeps()
        self.assertTrue(pump(lambda: len(self.generator.calls) == 2))
        self.assertEqual(self.generator.calls[1], [1, 3])


class TestPermanentFailure(SweepTestCase):
    def drive_until_settled(self, limit=40):
        for _ in range(limit):
            self.tick()
            if self.fire_scheduled_sweeps():
                pump(lambda: self.generation.get_progress_info()[0] >= self.CHUNKS)
            if self.controller._finalize_generation.called or self.task.status == TaskStatus.FAILED:
                return
        self.fail("monitor never settled")

    def test_chunk_that_never_succeeds_gets_exactly_five_sequential_sweeps_then_fails(self):
        self.generator.fail_attempts = {2: INF}
        self.run_initial_pass()

        self.drive_until_settled()

        self.assertEqual(self.generator.attempts[2], 1 + 5)
        self.assertEqual(self.generator.max_concurrent_per_chunk, 1)
        self.assertEqual(self.task.status, TaskStatus.FAILED)
        self.controller._finalize_generation.assert_not_called()

    def test_failed_task_after_sweeps_is_retryable(self):
        """Ties C4 to C2: the task that exhausted its sweeps must remain in the queue for Retry."""
        self.generator.fail_attempts = {2: INF}
        self.run_initial_pass()
        self.drive_until_settled()

        self.assertIn(self.task, self.queue.get_all_tasks())
        self.assertIsNone(self.queue.get_current_task())
        self.assertIs(self.queue.retry_task(str(self.task.id)), self.task)

    def test_failure_message_names_the_sweep_limit_and_chunk_count(self):
        self.generator.fail_attempts = {2: INF}
        self.run_initial_pass()
        errors = []
        self.controller.errorOccurred.connect(errors.append)
        self.drive_until_settled()
        self.assertEqual(len(errors), 1)
        self.assertIn("1 chunk(s)", errors[0])
        self.assertIn("5 retry sweeps", errors[0])

    def test_failed_count_is_accurate_after_sweeps(self):
        self.generator.fail_attempts = {2: INF}
        self.run_initial_pass()
        self.drive_until_settled()
        tracker = self.generation._progress_tracker
        self.assertEqual(tracker.get_failed_count(), 1)
        self.assertEqual(tracker.get_completed_count(), 2)
        self.assertEqual(tracker.get_processed_count(), 3)

    def test_chunk_that_succeeds_on_third_attempt_does_not_fail_the_task(self):
        self.generator.fail_attempts = {2: 2}
        self.run_initial_pass()
        self.drive_until_settled()
        self.controller._finalize_generation.assert_called_once()
        self.assertNotEqual(self.task.status, TaskStatus.FAILED)
        self.assertEqual(self.generator.attempts[2], 3)


class TestBatchErrorAccounting(unittest.TestCase):
    """A batch-level exception after partial success must not double-count chunks."""

    def test_exception_after_partial_success_keeps_counts_consistent(self):
        class Exploding(FakeGenerator):
            def generate_audio_batch(self, batch, *a, chunk_callback=None, **kw):
                first = batch[0]
                chunk_callback(first.chunk_number, "/x.mp3", 1.0)
                raise RuntimeError("batch crashed")

        service = GenerationService(Exploding())
        with tempfile.TemporaryDirectory() as tmp:
            task = MagicMock()
            task.config.effective_thread_count = 1
            task.config.language, task.config.gender = "en", "male"
            chunks = [AudioChunk(chunk_number=i, text_content="t") for i in (1, 2, 3)]
            service.start_generation(task, chunks, tmp)
            self.assertTrue(pump(lambda: service.get_progress_info()[0] >= 3))
            service.stop(wait=True)

        tracker = service._progress_tracker
        self.assertEqual(tracker.get_completed_count(), 1)
        self.assertEqual(tracker.get_failed_count(), 2)
        self.assertEqual(tracker.get_processed_count(), 3)


if __name__ == "__main__":
    unittest.main()
