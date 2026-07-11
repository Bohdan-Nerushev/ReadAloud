import unittest
import sys
from unittest.mock import MagicMock, patch
from pathlib import Path

# Mock PyQt6 to avoid imports crashing
mock_qt = MagicMock()
sys.modules['PyQt6.QtCore'] = mock_qt
sys.modules['PyQt6.QtWidgets'] = mock_qt
sys.modules['src.gui.styles'] = MagicMock()

from src.main import ReadAloudApplication
from src.domain.models import ProjectConfig, TaskStatus, GenerationTask
from src.domain.exceptions import ConfigurationException

class TestGUI(unittest.TestCase):
    def setUp(self):
        self.mock_window = MagicMock()
        self.mock_controller = MagicMock()
        
        # We need to ensure coordinator setup doesn't fail
        # Connector tries to connect signals
        self.mock_window.control_buttons.startClicked = MagicMock()
        self.mock_window.queue_list.taskDeleteRequested = MagicMock()
        
        self.app = ReadAloudApplication(self.mock_window, self.mock_controller)

    @patch('src.main.QMessageBox')
    def test_project_name_validation(self, mock_msgbox):
        """test_project_name_validation: Verification of project name validator logic."""
        # Setup mock behavior for UI methods
        self.mock_window.project_input.get_project_name.return_value = "Invalid/Name"
        self.mock_window.file_selector.get_selected_file.return_value = "/mock/test.txt"
        self.mock_window.output_selector.get_selected_directory.return_value = "/mock/out"
        self.mock_window.language_selector.get_selected_language.return_value = "en"
        self.mock_window.gender_selector.get_selected_gender.return_value = "male"
        self.mock_window.speed_selector.get_selected_speed.return_value = 1.0
        self.mock_window.thread_selector.get_thread_count.return_value = 1
        
        with patch('src.domain.models.Path') as mock_path:
            mock_path.return_value.exists.return_value = True
            mock_path.return_value.is_file.return_value = True
            mock_path.return_value.is_dir.return_value = True
            
            # This should trigger ConfigurationException in _build_config_from_ui 
            # and show QMessageBox.warning
            self.app._on_start_clicked()
            
            mock_msgbox.warning.assert_called_once()
            call_args = mock_msgbox.warning.call_args[0]
            self.assertIn("Project name contains invalid characters", call_args[2])

    @patch('src.main.QMessageBox')
    def test_file_path_existence_validation(self, mock_msgbox):
        """test_file_path_existence_validation: Verification of response to selecting a non-existent file."""
        self.mock_window.project_input.get_project_name.return_value = "ValidName"
        self.mock_window.file_selector.get_selected_file.return_value = "/non/existent/file.txt"
        self.mock_window.output_selector.get_selected_directory.return_value = "/mock/out"
        self.mock_window.language_selector.get_selected_language.return_value = "en"
        self.mock_window.gender_selector.get_selected_gender.return_value = "male"
        self.mock_window.speed_selector.get_selected_speed.return_value = 1.0
        self.mock_window.thread_selector.get_thread_count.return_value = 1
        
        with patch('src.domain.models.Path') as mock_path:
            mock_path.return_value.exists.return_value = False
            
            self.app._on_start_clicked()
            
            mock_msgbox.warning.assert_called_once()
            call_args = mock_msgbox.warning.call_args[0]
            self.assertIn("does not exist", call_args[2])

    def test_dynamic_ui_updates(self):
        """test_dynamic_ui_updates: Verification of button state synchronization with the queue state."""
        mock_task = MagicMock(spec=GenerationTask)
        
        # 1. Test running state
        mock_task.status = TaskStatus.PROCESSING
        self.app._on_task_updated(mock_task)
        self.mock_window.control_buttons.set_running_state.assert_called_once()
        
        # 2. Test paused state
        self.mock_window.control_buttons.set_running_state.reset_mock()
        mock_task.status = TaskStatus.PAUSED
        self.app._on_task_updated(mock_task)
        self.mock_window.control_buttons.set_paused_state.assert_called_once()
        
        # 3. Test queue status changed to active
        self.app._on_queue_status_changed(True)
        self.mock_window.control_buttons.set_running_state.assert_called_once()
        self.mock_window.progress_display.show.assert_called_once()
        
        # 4. Test queue status changed to idle
        self.app._on_queue_status_changed(False)
        self.mock_window.control_buttons.set_idle_state.assert_called_once()

    def test_task_deletion_calls_controller(self):
        """test_task_deletion_calls_controller: Verification of controller call upon task deletion request."""
        task_id = "test-uuid"
        self.app._on_task_delete_requested(task_id)
        self.mock_controller.cancel_task.assert_called_once_with(task_id)
        self.mock_window.queue_list.remove_task.assert_called_once_with(task_id)

if __name__ == '__main__':
    unittest.main()
