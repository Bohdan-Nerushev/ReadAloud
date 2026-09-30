"""ProgressTracker per-chunk accounting (regression tests for retry-sweep miscounting)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.infrastructure.progress_tracker import ProgressTracker


class TestChunkAccounting(unittest.TestCase):
    def setUp(self):
        self.tracker = ProgressTracker(total_chunks=10)
        self.tracker.start()

    def _run_initial_pass(self, failed=(9, 10)):
        for n in range(1, 11):
            self.tracker.update_progress(n not in failed, chunk_number=n)

    def test_initial_pass_counts_successes_and_failures(self):
        self._run_initial_pass()
        self.assertEqual(self.tracker.get_completed_count(), 8)
        self.assertEqual(self.tracker.get_failed_count(), 2)
        self.assertEqual(self.tracker.get_processed_count(), 10)

    def test_duplicate_success_is_not_counted_twice(self):
        self.tracker.update_progress(True, chunk_number=1)
        self.tracker.update_progress(True, chunk_number=1)
        self.assertEqual(self.tracker.get_completed_count(), 1)
        self.assertEqual(self.tracker.get_processed_count(), 1)

    def test_duplicate_failure_is_not_counted_twice(self):
        self.tracker.update_progress(False, chunk_number=4)
        self.tracker.update_progress(False, chunk_number=4)
        self.assertEqual(self.tracker.get_failed_count(), 1)

    def test_late_success_replaces_failure(self):
        self.tracker.update_progress(False, chunk_number=3)
        self.tracker.update_progress(True, chunk_number=3)
        self.assertEqual(self.tracker.get_failed_count(), 0)
        self.assertEqual(self.tracker.get_completed_count(), 1)
        self.assertEqual(self.tracker.get_processed_count(), 1)

    def test_failure_never_overrides_success(self):
        """A batch-level error reported after a chunk already succeeded must not double count it."""
        self.tracker.update_progress(True, chunk_number=2)
        self.tracker.update_progress(False, chunk_number=2)
        self.assertEqual(self.tracker.get_completed_count(), 1)
        self.assertEqual(self.tracker.get_failed_count(), 0)
        self.assertEqual(self.tracker.get_processed_count(), 1)

    def test_processed_never_exceeds_total(self):
        self._run_initial_pass(failed=())
        for n in range(1, 11):
            self.tracker.update_progress(False, chunk_number=n)
            self.tracker.update_progress(True, chunk_number=n)
        self.assertEqual(self.tracker.get_processed_count(), 10)
        self.assertEqual(self.tracker.get_progress_percentage(), 100.0)


class TestBeginRetry(unittest.TestCase):
    def setUp(self):
        self.tracker = ProgressTracker(total_chunks=10)
        self.tracker.start()
        for n in range(1, 11):
            self.tracker.update_progress(n not in (9, 10), chunk_number=n)

    def test_retry_makes_progress_incomplete_until_results_arrive(self):
        """The bug: processed stayed == total during a sweep, so monitors started another one."""
        self.assertGreaterEqual(self.tracker.get_processed_count(), 10)

        self.tracker.begin_retry([9, 10])

        self.assertEqual(self.tracker.get_processed_count(), 8)
        self.assertEqual(self.tracker.get_failed_count(), 0)
        self.assertLess(self.tracker.get_processed_count(), self.tracker.get_total_count())

    def test_sweep_results_complete_progress(self):
        self.tracker.begin_retry([9, 10])
        self.tracker.update_progress(True, chunk_number=9)
        self.assertEqual(self.tracker.get_processed_count(), 9)
        self.tracker.update_progress(False, chunk_number=10)
        self.assertEqual(self.tracker.get_processed_count(), 10)
        self.assertEqual(self.tracker.get_completed_count(), 9)
        self.assertEqual(self.tracker.get_failed_count(), 1)

    def test_failed_counter_decrements_after_successful_retry(self):
        self.assertEqual(self.tracker.get_failed_count(), 2)
        self.tracker.begin_retry([9, 10])
        self.tracker.update_progress(True, chunk_number=9)
        self.tracker.update_progress(True, chunk_number=10)
        self.assertEqual(self.tracker.get_failed_count(), 0)
        self.assertEqual(self.tracker.get_completed_count(), 10)

    def test_retry_of_a_completed_chunk_removes_it_from_completed(self):
        """A chunk whose file vanished from disk is resynthesized and stops counting as done."""
        self.tracker.begin_retry([1])
        self.assertEqual(self.tracker.get_completed_count(), 7)

    def test_begin_retry_with_no_chunks_changes_nothing(self):
        self.tracker.begin_retry([])
        self.assertEqual(self.tracker.get_processed_count(), 10)

    def test_begin_retry_caps_anonymous_counters(self):
        """Counters restored from disk without ids (start(completed_chunks=N)) stay consistent."""
        tracker = ProgressTracker(total_chunks=10)
        tracker.start(completed_chunks=10)
        tracker.begin_retry([4, 5])
        self.assertEqual(tracker.get_processed_count(), 8)

    def test_legacy_anonymous_updates_still_work(self):
        tracker = ProgressTracker(total_chunks=5)
        tracker.update_progress(True)
        tracker.update_progress(True)
        tracker.update_progress(False)
        self.assertEqual(tracker.get_completed_count(), 2)
        self.assertEqual(tracker.get_failed_count(), 1)

    def test_start_clears_previous_chunk_state(self):
        self.tracker.start()
        self.assertEqual(self.tracker.get_processed_count(), 0)


if __name__ == "__main__":
    unittest.main()
