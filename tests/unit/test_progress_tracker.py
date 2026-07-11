import unittest
import sys
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.infrastructure.progress_tracker import ProgressTracker

class TestProgressTracker(unittest.TestCase):
    def test_update_progress_percentage(self):
        """test_update_progress_percentage: Verifies correct calculation of progress percentage."""
        tracker = ProgressTracker(total_chunks=10)
        tracker.update_progress(success=True)
        tracker.update_progress(success=True)
        # 2/10 = 20%
        self.assertEqual(tracker.get_progress_percentage(), 20.0)
        
        tracker.update_progress(success=False)
        # 3/10 = 30%
        self.assertEqual(tracker.get_progress_percentage(), 30.0)

    def test_get_eta_string_valid_estimation(self):
        """test_get_eta_string_valid_estimation: Verifies estimation of completion time (ETA string)."""
        tracker = ProgressTracker(total_chunks=10)
        tracker.start()
        
        # Simulate processing 2 chunks taking 2 seconds each
        # We mock datetime to avoid actual waiting
        with patch('src.infrastructure.progress_tracker.datetime') as mock_dt:
            start_time = datetime(2023, 1, 1, 12, 0, 0)
            # When start() was called, it used datetime.now()
            # We need to ensure we return matching times
            
            # Initial call in start() (now done in test, we need to re-init)
            mock_dt.now.return_value = start_time
            tracker.start()
            
            # Update progress after 4 seconds (2 chunks)
            mock_dt.now.return_value = start_time + timedelta(seconds=4)
            tracker.update_progress(success=True)
            tracker.update_progress(success=True)
            
            # Remaining: 8 chunks. 2 chunks took 4s => 2s/chunk.
            # 8 chunks * 2s = 16s remaining.
            # ETA string should be 00:00:16
            
            # In get_eta_string, it calls get_estimated_completion_time which calls datetime.now() again
            # And then get_eta_string calls datetime.now() to calculate 'remaining'
            mock_dt.now.return_value = start_time + timedelta(seconds=4)
            
            eta_str = tracker.get_eta_string()
            self.assertEqual(eta_str, "00:00:16")

    def test_get_eta_string_no_data(self):
        """test_get_eta_string_no_data: Verifies returning '...' when no progress data is available."""
        tracker = ProgressTracker(total_chunks=10)
        # No start() called, or no chunks updated
        self.assertEqual(tracker.get_eta_string(), "...")
        
        tracker.start()
        self.assertEqual(tracker.get_eta_string(), "...")

    def test_thread_safety(self):
        """test_thread_safety: Verifies thread-safety of counters under multi-threaded updates."""
        tracker = ProgressTracker(total_chunks=1000)
        
        def worker():
            for _ in range(100):
                tracker.update_progress(success=True)
        
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        
        self.assertEqual(tracker.get_completed_count(), 1000)
        self.assertEqual(tracker.get_progress_percentage(), 100.0)

    def test_invalid_total_chunks(self):
        """test_invalid_total_chunks: Verifies ValueError is raised for total_chunks <= 0."""
        with self.assertRaises(ValueError):
            ProgressTracker(total_chunks=0)
        with self.assertRaises(ValueError):
            ProgressTracker(total_chunks=-5)

from unittest.mock import patch

if __name__ == '__main__':
    unittest.main()
