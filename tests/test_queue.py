
import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock dependencies
sys.modules['edge_tts'] = MagicMock()
mock_qt = MagicMock()
class MockQObject:
    def __init__(self, *args, **kwargs): pass
class MockQThread(MockQObject):
    finished = MagicMock()
    error = MagicMock()
    def start(self): pass
    def terminate(self): pass
    def wait(self): pass
    def isRunning(self): return False
class MockQTimer(MockQObject):
    timeout = MagicMock()
    def start(self, *args): pass
    def stop(self): pass
def mock_signal(*args):
    s = MagicMock()
    s.connect = MagicMock()
    s.emit = MagicMock()
    return s

mock_qt.QObject = MockQObject
mock_qt.QThread = MockQThread
mock_qt.QTimer = MockQTimer
mock_qt.pyqtSignal = mock_signal

sys.modules['PyQt6.QtCore'] = mock_qt
sys.modules['PyQt6.QtWidgets'] = MagicMock()

from src.domain.models import ProjectConfig, GenerationTask, TaskStatus
from src.application.app_controller import ApplicationController

class TestQueueSystem(unittest.TestCase):
    def setUp(self):
        self.controller = ApplicationController()
        # Mock internal components to prevent actual execution logic
        self.controller._prep_worker = MagicMock()
        self.controller._audio_generator = MagicMock()
        self.controller._file_manager = MagicMock()
        self.controller._text_processor = MagicMock()
        self.controller._text_chunker = MagicMock()
        
        # Mock timers
        self.controller._progress_timer = MagicMock()
        self.controller._completion_timer = MagicMock()
        
        # Create dummy files for validation
        self.test_file1 = Path("/tmp/test.txt")
        self.test_file1.touch()
        self.test_file2 = Path("/tmp/test2.txt")
        self.test_file2.touch()
        Path("/tmp").mkdir(exist_ok=True)
        
        self.config = ProjectConfig(
            project_name="TestProject",
            input_file_path=str(self.test_file1),
            language="en",
            gender="male",
            thread_count=1,
            output_dir_path="/tmp",
            speed=1.0
        )

    def tearDown(self):
        if self.test_file1.exists():
            self.test_file1.unlink()
        if self.test_file2.exists():
            self.test_file2.unlink()

    def test_add_task_starts_processing_if_idle(self):
        print("\nTesting: Add task starts processing if idle")
        self.controller.add_task(self.config)
        
        self.assertIsNotNone(self.controller._current_task)
        self.assertEqual(self.controller._current_task.config.project_name, "TestProject")
        self.assertEqual(self.controller._current_task.status, TaskStatus.PROCESSING)
        
        # Verify queue is empty (popped)
        self.assertEqual(len(self.controller._task_queue), 0)

    def test_add_task_queues_if_busy(self):
        print("Testing: Add task queues if busy")
        # Start first task
        self.controller.add_task(self.config)
        
        # Create second config
        config2 = ProjectConfig(
            project_name="Project2",
            input_file_path=str(self.test_file2),
            language="en",
            gender="female",
            thread_count=1,
            output_dir_path="/tmp",
            speed=1.0
        )
        
        self.controller.add_task(config2)
        
        # Verify first is still processing
        self.assertEqual(self.controller._current_task.config.project_name, "TestProject")
        
        # Verify second is in queue
        self.assertEqual(len(self.controller._task_queue), 1)
        queued_task = self.controller._task_queue[0]
        self.assertEqual(queued_task.config.project_name, "Project2")
        self.assertEqual(queued_task.status, TaskStatus.PENDING)

    def test_sequential_processing(self):
        print("Testing: Sequential processing")
        self.controller.add_task(self.config)
        
        config2 = ProjectConfig(
            project_name="Project2",
            input_file_path=str(self.test_file2),
            language="en",
            gender="female",
            thread_count=1,
            output_dir_path="/tmp",
            speed=1.0
        )
        self.controller.add_task(config2)
        
        # Simulate completion of task 1
        with patch.object(self.controller, '_process_queue', wraps=self.controller._process_queue) as mock_process:
            self.controller._finalize_task()
            
            # Should have called process_queue
            self.assertTrue(mock_process.called)
            
            # Now current task should be Project2
            self.assertIsNotNone(self.controller._current_task)
            self.assertEqual(self.controller._current_task.config.project_name, "Project2")
            self.assertEqual(len(self.controller._task_queue), 0)

    def test_stop_generation_handles_current_task(self):
        print("Testing: Stop generation logic")
        self.controller.add_task(self.config)
        
        self.controller.stop_generation()
        
        self.assertTrue(self.controller._is_stopped)
        # Note: stop_generation calls _finalize_task which clears _current_task
        # So we can't check _current_task status unless we mock _finalize_task
        # But we can verify it called finalize
        
        self.assertIsNone(self.controller._current_task)

if __name__ == '__main__':
    unittest.main()
