import unittest
import sys
import threading
import time
from unittest.mock import MagicMock, patch
from pathlib import Path

# Mock PyQt6
mock_qt = MagicMock()
class MockQObject:
    def __init__(self, *args, **kwargs): pass
mock_qt.QObject = MockQObject
def mock_signal(*args):
    s = MagicMock()
    s.connect = MagicMock()
    s.emit = MagicMock()
    return s
mock_qt.pyqtSignal = mock_signal
sys.modules['PyQt6.QtCore'] = mock_qt
sys.modules['PyQt6.QtWidgets'] = MagicMock()

from src.application.services.queue_service import QueueService
from src.domain.models import ProjectConfig, TaskStatus
from src.application.app_controller import ApplicationController

class TestConcurrency(unittest.TestCase):
    def setUp(self):
        self.queue_service = QueueService()
        
        # Mock Path for safety
        self.path_patcher = patch('src.domain.models.Path')
        self.mock_path = self.path_patcher.start()
        self.mock_path_instance = self.mock_path.return_value
        self.mock_path_instance.exists.return_value = True
        self.mock_path_instance.is_file.return_value = True
        self.mock_path_instance.is_dir.return_value = True

        self.config = ProjectConfig(
            project_name="ConcurrentTask",
            input_file_path="/mock/in.txt",
            language="en",
            gender="male",
            thread_count=1,
            output_dir_path="/mock/out",
            speed=1.0
        )

    def tearDown(self):
        self.path_patcher.stop()

    def test_concurrent_queue_management(self):
        """
        test_concurrent_queue_management: Одночасне додавання великої кількості завдань з різних потоків.
        """
        num_threads = 10
        tasks_per_thread = 50
        
        def adder():
            for _ in range(tasks_per_thread):
                self.queue_service.add_task(self.config)
        
        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=adder)
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        all_tasks = self.queue_service.get_all_tasks()
        self.assertEqual(len(all_tasks), num_threads * tasks_per_thread)

    def test_rapid_cancel_during_processing(self):
        """
        test_rapid_cancel_during_processing: Скасування завдання саме в момент активної генерації.
        """
        # Create dependencies (mocked or real as needed)
        # For cancel test, we want to ensure stop calls on services are made.
        mock_gen_service = MagicMock()
        mock_asm_service = MagicMock()
        
        # Mock get_progress_info to return expected 4-tuple
        mock_gen_service.get_progress_info.return_value = (0, 0, "00:00:00", 0.0)
        mock_gen_service.get_progress_percentage.return_value = 0.0
        
        controller = ApplicationController(
            self.queue_service,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            mock_gen_service,
            mock_asm_service,
            MagicMock()
        )

        
        # Start a task - mock start_task to avoid threads
        with patch.object(controller, '_start_task', return_value=True):
            controller.add_task(self.config)
            
            # Rapidly stop
            controller.stop_generation()
            
            # Verify state
            self.assertTrue(controller._is_stopped)
            self.assertIsNone(controller._get_current_task())
        
        # Verify services are stopped
        mock_gen_service.stop.assert_called()
        mock_asm_service.stop.assert_called()
        
        # Try adding another task after stop
        controller.add_task(self.config)
        self.assertFalse(controller._is_stopped) # Should reset on new task
        self.assertEqual(controller._get_current_task().config.project_name, "ConcurrentTask")

if __name__ == '__main__':
    unittest.main()
