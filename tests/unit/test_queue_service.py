import unittest
import sys
from unittest.mock import MagicMock, patch

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
from src.domain.models import ProjectConfig, GenerationTask, TaskStatus
from src.domain.exceptions import ConfigurationException

class TestQueueService(unittest.TestCase):
    def setUp(self):
        self.queue_service = QueueService()
        
        # Mocking Path for ProjectConfig validation
        self.path_patcher = patch('src.domain.models.Path')
        self.mock_path = self.path_patcher.start()
        self.mock_path_instance = self.mock_path.return_value
        self.mock_path_instance.exists.return_value = True
        self.mock_path_instance.is_file.return_value = True
        self.mock_path_instance.is_dir.return_value = True
        
        self.config = ProjectConfig(
            project_name="TestProject",
            input_file_path="/mock/input.txt",
            language="en",
            gender="male",
            thread_count=1,
            output_dir_path="/mock/output",
            speed=1.0
        )

    def tearDown(self):
        self.path_patcher.stop()

    def test_add_task_sequence(self):
        """test_add_task_sequence: Перевірка коректної нумерації та черговості доданих завдань."""
        task1 = self.queue_service.add_task(self.config)
        task2 = self.queue_service.add_task(self.config)
        
        all_tasks = self.queue_service.get_all_tasks()
        
        # Since we use get_all_tasks, it includes current_task and task_queue
        # Initial state: current_task=None, queue=[task1, task2]
        self.assertEqual(len(all_tasks), 2)
        self.assertEqual(all_tasks[0], task1)
        self.assertEqual(all_tasks[1], task2)
        
        # Verify order when retrieving next task
        retrieved1 = self.queue_service.get_next_task()
        self.assertEqual(retrieved1, task1)
        
        self.queue_service.finalize_current_task()
        
        retrieved2 = self.queue_service.get_next_task()
        self.assertEqual(retrieved2, task2)

    def test_status_transition_validation(self):
        """test_status_transition_validation: Валідація дозволених переходів станів."""
        task = self.queue_service.add_task(self.config)
        self.queue_service.get_next_task() # Sets as current_task
        
        # Initial status is PENDING (from GenerationTask default) or determined by get_next_task?
        # Actually add_task creates a task with PENDING.
        # get_next_task sets it as _current_task but doesn't change status to PROCESSING automatically.
        # It's usually the service or controller that does it.
        
        # Test valid transition
        self.queue_service.update_task_status(TaskStatus.PROCESSING)
        self.assertEqual(task.status, TaskStatus.PROCESSING)
        
        # Test terminal state validation (added logic to models.py)
        self.queue_service.update_task_status(TaskStatus.COMPLETED)
        
        with self.assertRaises(ValueError):
            self.queue_service.update_task_status(TaskStatus.PROCESSING)

    def test_subscriber_notifications(self):
        """test_subscriber_notifications: Перевірка отримування сигналів при зміні стану черги."""
        added_mock = MagicMock()
        updated_mock = MagicMock()
        status_mock = MagicMock()
        
        self.queue_service.subscribe_task_added(added_mock)
        self.queue_service.subscribe_task_updated(updated_mock)
        self.queue_service.subscribe_status_changed(status_mock)
        
        # Add task triggers added_mock
        task = self.queue_service.add_task(self.config)
        added_mock.assert_called_once_with(task)
        
        # get_next_task triggers status_mock(True)
        self.queue_service.get_next_task()
        status_mock.assert_called_with(True)
        
        # Update progress triggers updated_mock
        self.queue_service.update_task_progress(50.0)
        updated_mock.assert_called_once_with(task)
        self.assertEqual(task.progress, 50.0)
        
        # Update status triggers updated_mock
        updated_mock.reset_mock()
        self.queue_service.update_task_status(TaskStatus.PROCESSING)
        updated_mock.assert_called_once_with(task)

if __name__ == '__main__':
    unittest.main()
