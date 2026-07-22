import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.application.app_controller import ApplicationController
from src.domain.models import ProjectConfig, GenerationTask, TaskStatus

class TestAppController(unittest.TestCase):
    def setUp(self):
        # Mock services
        self.queue_service = MagicMock()
        self.text_processor = MagicMock()
        self.text_chunker = MagicMock()
        self.file_manager = MagicMock()
        self.generation_service = MagicMock()
        self.assembly_service = MagicMock()
        
        # Mock Path.exists and Path.is_file to avoid ConfigurationException in ProjectConfig
        self.patcher_exists = patch('src.domain.models.Path.exists', return_value=True)
        self.patcher_is_file = patch('src.domain.models.Path.is_file', return_value=True)
        self.patcher_exists.start()
        self.patcher_is_file.start()
        
        self.config = ProjectConfig(
            project_name="Test",
            input_file_path="/tmp/in.txt",
            language="en",
            gender="male",
            speed=1.0,
            thread_count=1,
            output_dir_path="/tmp"
        )

        # Mock QueueService to avoid division by zero in _emit_global_progress
        self.task_1 = GenerationTask(id="1", config=self.config)
        self.queue_service.get_all_tasks.return_value = [self.task_1]
        
        # Ensure status updates are reflected on the mock task
        def mock_update_status(status, msg=""):
            self.task_1.status = status
        self.queue_service.update_task_status.side_effect = mock_update_status

        # Mock GenerationService.get_progress_info to return expected 4-tuple
        self.generation_service.get_progress_info.return_value = (0, 0, "...", 0.0)
        
        # Initialize controller with mocked services
        self.controller = ApplicationController(
            queue_service=self.queue_service,
            text_processor=self.text_processor,
            text_chunker=self.text_chunker,
            file_manager=self.file_manager,
            generation_service=self.generation_service,
            assembly_service=self.assembly_service,
            persistence_service=MagicMock()
        )


    def tearDown(self):
        self.patcher_exists.stop()
        self.patcher_is_file.stop()

    def test_orchestration_flow_add_task(self):
        """test_orchestration_flow_add_task: Verifies that QueueService is called when a task is added."""
        self.controller.add_task(self.config)
        self.queue_service.add_task.assert_called_once()
        
    @patch('src.application.app_controller.PreparationWorker')
    def test_start_task_initializes_and_starts_worker(self, mock_worker_class):
        """test_start_task_initializes_and_starts_worker: Verifies that the PreparationWorker is initialized and started."""
        mock_worker = MagicMock()
        mock_worker_class.return_value = mock_worker
        
        # In real controller, _start_task sets status to PROCESSING
        self.controller._start_task(self.task_1)
        
        self.assertEqual(self.task_1.status, TaskStatus.PROCESSING)
        mock_worker_class.assert_called_once()
        mock_worker.start.assert_called_once()

    def test_on_preparation_finished_starts_generation(self):
        """test_on_preparation_finished_starts_generation: Verifies transition to audio generation after preparation is finished."""
        self.task_1.status = TaskStatus.PROCESSING
        self.controller._current_task = self.task_1
        
        chunks = [MagicMock()]
        self.controller._on_preparation_finished(chunks, "/tmp/text", "/tmp/audio")
        
        self.generation_service.start_generation.assert_called_once()
        self.assertEqual(self.task_1.status, TaskStatus.PROCESSING)

    def test_on_preparation_error_handles_failure(self):
        """test_on_preparation_error_handles_failure: Verifies error handling when task preparation fails."""
        # Set to PROCESSING so _finalize_task can progress but let's check marking
        self.task_1.status = TaskStatus.PROCESSING
        self.controller._current_task = self.task_1
        
        # We mock _finalize_task to avoid complex side effects and check update_task_status
        with patch.object(self.controller, '_finalize_task'):
            self.controller._on_preparation_error("Failed")
            self.assertEqual(self.task_1.status, TaskStatus.FAILED)
            self.queue_service.update_task_status.assert_called_with(TaskStatus.FAILED, "Failed")

    def test_initialize_task_state_emits_correct_signal(self):
        """test_initialize_task_state_emits_correct_signal: Verifies that the progressUpdated signal is emitted with 4 arguments on initialization."""
        mock_slot = MagicMock()
        self.controller.progressUpdated.connect(mock_slot)
        self.controller._initialize_task_state(self.task_1)
        mock_slot.assert_called_with(0, 0, "Preparing...", 0.0)

    def test_restore_state_active_task(self):
        """test_restore_state_active_task: Verifies restore_state handles active tasks and emits statusChanged."""
        self.task_1.status = TaskStatus.PROCESSING
        self.controller._persistence_service.load_state.return_value = [self.task_1]
        
        mock_signal = MagicMock()
        self.queue_service.statusChanged = mock_signal
        
        self.controller.restore_state()
        
        mock_signal.emit.assert_called_once_with(True)
        self.assertEqual(self.task_1.status, TaskStatus.PAUSED)

    def test_prepare_output_path_adds_timestamp(self):
        """test_prepare_output_path_adds_timestamp: Verifies that output file has the timestamp appended to prevent overwrites."""
        output_path = self.controller._prepare_output_path(self.task_1)
        self.assertTrue(output_path.name.startswith("Test_"))
        self.assertTrue(output_path.name.endswith(".mp3"))
        name_without_ext = output_path.stem
        parts = name_without_ext.split('_')
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], "Test")
        self.assertEqual(len(parts[1]), 8)
        self.assertEqual(len(parts[2]), 6)

if __name__ == '__main__':
    unittest.main()
