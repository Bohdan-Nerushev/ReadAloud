import unittest
import time
import threading
from src.infrastructure.thread_manager import ThreadManager, ThreadState

class TestThreadManager(unittest.TestCase):
    def test_basic_execution(self):
        """Test that tasks are executed and result can be retrieved."""
        manager = ThreadManager(thread_count=2)
        manager.start()
        
        def mock_task(x):
            return x * 2
            
        future = manager.submit_task(mock_task, 21)
        self.assertEqual(future.result(timeout=1.0), 42)
        manager.stop()

    def test_pause_resume(self):
        """Test that pause prevents task from starting and resume allows it."""
        manager = ThreadManager(thread_count=1)
        manager.start()
        
        event = threading.Event()
        def task_with_event():
            event.set()
            return True
            
        manager.pause()
        future = manager.submit_task(task_with_event)
        
        # Should not be executed within 100ms
        self.assertFalse(event.wait(timeout=0.1))
        
        manager.resume()
        self.assertTrue(event.wait(timeout=1.0))
        self.assertTrue(future.result())
        manager.stop()

    def test_memory_leak_fix(self):
        """Test that completed futures are removed from the list."""
        manager = ThreadManager(thread_count=4)
        manager.start()
        
        def dummy_task():
            return "done"
            
        # Submit many tasks
        for _ in range(20):
            future = manager.submit_task(dummy_task)
            future.result() # Wait for completion
            
        # After 20 tasks, if cleanup works, the list should be very small
        # (potentially 1 if the last one just finished)
        self.assertLess(len(manager._futures), 5)
        
        manager.stop()

if __name__ == '__main__':
    unittest.main()
