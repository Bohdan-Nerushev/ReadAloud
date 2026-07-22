
import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import sys
from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.domain.models import ProjectConfig, GenerationTask, TaskStatus
from src.application.app_controller import ApplicationController
from src.application.services.queue_service import QueueService

class TestQueueSystem(unittest.TestCase):
    def setUp(self):
        queue_service = QueueService()

        text_processor = MagicMock()
        text_chunker = MagicMock()
        file_manager = MagicMock()
        generation_service = MagicMock()
        generation_service.get_progress_info.return_value = (0, 0, "", 0.0)
        assembly_service = MagicMock()
        persistence_service = MagicMock()

        self.controller = ApplicationController(
            queue_service=queue_service,
            text_processor=text_processor,
            text_chunker=text_chunker,
            file_manager=file_manager,
            generation_service=generation_service,
            assembly_service=assembly_service,
            persistence_service=persistence_service
        )


        # Mock internal components to prevent actual execution logic
        self.controller._start_preparation_worker = MagicMock()
        self.controller._prep_worker = MagicMock()
        
        # Mock timers
        self.controller._progress_timer = MagicMock()
        self.controller._completion_monitor_timer = MagicMock()
        
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
        self.controller.add_task(self.config)
        
        self.assertIsNotNone(self.controller._queue_service._current_task)
        self.assertEqual(self.controller._queue_service._current_task.config.project_name, "TestProject")
        self.assertEqual(self.controller._queue_service._current_task.status, TaskStatus.PROCESSING)
        
        # Verify queue is empty (popped)
        self.assertEqual(len(self.controller._queue_service._task_queue), 0)


    def test_add_task_queues_if_busy(self):
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
        self.assertEqual(self.controller._queue_service._current_task.config.project_name, "TestProject")
        
        # Verify second is in queue
        self.assertEqual(len(self.controller._queue_service._task_queue), 1)
        queued_task = self.controller._queue_service._task_queue[0]
        self.assertEqual(queued_task.config.project_name, "Project2")
        self.assertEqual(queued_task.status, TaskStatus.PENDING)


    def test_sequential_processing(self):
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
            self.assertIsNotNone(self.controller._queue_service._current_task)
            self.assertEqual(self.controller._queue_service._current_task.config.project_name, "Project2")
            self.assertEqual(len(self.controller._queue_service._task_queue), 0)


    def test_stop_generation_handles_current_task(self):
        self.controller.add_task(self.config)
        
        self.controller.stop_generation()
        
        self.assertTrue(self.controller._is_stopped)
        # Note: stop_generation calls _finalize_task which clears _current_task
        # So we can't check _current_task status unless we mock _finalize_task
        # But we can verify it called finalize
        
        self.assertIsNone(self.controller._queue_service._current_task)


if __name__ == '__main__':
    unittest.main()
