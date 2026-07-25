import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.main import ReadAloudApplication
from src.gui.widgets.file_selector import FileSelectorWidget


class TestMultiFileSelector(unittest.TestCase):
    def setUp(self):
        self.mock_window = MagicMock()
        self.mock_controller = MagicMock()
        self.mock_window.control_buttons.startClicked = MagicMock()
        self.mock_window.queue_list.taskDeleteRequested = MagicMock()
        self.app = ReadAloudApplication(self.mock_window, self.mock_controller)

    def test_multi_file_selector_methods(self):
        """Verifies get_selected_files and get_selected_file behavior in FileSelectorWidget."""
        widget = FileSelectorWidget()
        self.assertEqual(widget.get_selected_files(), [])
        self.assertEqual(widget.get_selected_file(), "")

        paths = ["/tmp/doc1.txt", "/tmp/doc2.txt"]
        widget._selected_files = paths

        self.assertEqual(widget.get_selected_files(), paths)
        self.assertEqual(widget.get_selected_file(), "/tmp/doc1.txt")

        widget.clear()
        self.assertEqual(widget.get_selected_files(), [])

    @patch('src.main.QMessageBox')
    def test_build_configs_for_multiple_files(self, mock_msgbox):
        """Verifies _build_configs_from_ui creates distinct ProjectConfigs named after file stems."""
        file_paths = ["/tmp/chapter1.txt", "/tmp/chapter2.txt"]
        self.mock_window.file_selector.get_selected_files.return_value = file_paths
        self.mock_window.output_selector.get_selected_directory.return_value = "/tmp/output"
        self.mock_window.language_selector.get_selected_language.return_value = "uk"
        self.mock_window.gender_selector.get_selected_gender.return_value = "male"
        self.mock_window.speed_selector.get_selected_speed.return_value = 1.0
        self.mock_window.thread_selector.get_thread_count.return_value = 4
        self.mock_window.project_input.get_project_name.return_value = "CustomProject"

        with patch('src.main.Path') as mock_path:
            mock_path.side_effect = lambda p: Path(p)
            with patch('src.domain.models.Path') as mock_domain_path:
                mock_domain_path.return_value.exists.return_value = True
                mock_domain_path.return_value.is_file.return_value = True
                mock_domain_path.return_value.is_dir.return_value = True

                configs = self.app._build_configs_from_ui()
                self.assertEqual(len(configs), 2)
                self.assertEqual(configs[0].project_name, "chapter1")
                self.assertEqual(configs[0].input_file_path, "/tmp/chapter1.txt")
                self.assertEqual(configs[1].project_name, "chapter2")
                self.assertEqual(configs[1].input_file_path, "/tmp/chapter2.txt")


if __name__ == '__main__':
    unittest.main()
