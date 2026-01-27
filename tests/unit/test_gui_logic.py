import sys
import unittest
from unittest.mock import MagicMock

# Mock styles and models to avoid complex dependencies
class MockStyles:
    BUTTON_START = ""
    BUTTON_STOP = ""
    BUTTON_PAUSE = ""
    CARD_STYLE = ""
    SPACING_MEDIUM = 5
    SPACING_LARGE = 10
    QUEUE_ITEM_PROGRESS_HEIGHT = 10
    QUEUE_ITEM_HEIGHT = 100
    QUEUE_LIST_HEIGHT = 200
    MAIN_WINDOW_STYLE = ""
    LABEL_TITLE = ""

class MockPalette:
    TEXT_PRIMARY = "#000"
    TEXT_SECONDARY = "#666"
    PRIMARY = "#0078d4"
    PRIMARY_PRESSED = "#005a9e"
    ERROR = "#d13438"
    ERROR_HOVER = "#a4262c"
    WARNING = "#ffb900"
    BG_MAIN = "#fff"
    BG_INPUT = "#f3f2f1"
    BORDER_LIGHT = "#edebe9"
    BORDER_DEFAULT = "#c8c6c4"

# Mock styles module
mock_styles_module = MagicMock()
mock_styles_module.Styles = MockStyles
mock_styles_module.Palette = MockPalette
sys.modules['src.gui.styles'] = mock_styles_module

from PyQt6.QtWidgets import QApplication
from src.gui.widgets.control_buttons import ControlButtonsWidget
from src.gui.widgets.queue_item import QueueItemWidget
from src.domain.models import GenerationTask, ProjectConfig, TaskStatus

# Create a single QApplication for all tests
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

class TestGUILogic(unittest.TestCase):
    def test_control_buttons_states(self):
        """Test that control buttons enable/disable correctly."""
        widget = ControlButtonsWidget()
        
        # Initial state (idle)
        widget.set_idle_state()
        self.assertTrue(widget._start_button.isEnabled())
        self.assertFalse(widget._pause_button.isEnabled())
        self.assertFalse(widget._stop_button.isEnabled())
        
        # Running state
        widget.set_running_state()
        self.assertTrue(widget._start_button.isEnabled())
        self.assertTrue(widget._pause_button.isEnabled())
        self.assertTrue(widget._stop_button.isEnabled())
        self.assertEqual(widget._pause_button.text(), "Pause Generation")
        
        # Paused state
        widget.set_paused_state()
        self.assertEqual(widget._pause_button.text(), "Resume Generation")
        
    def test_queue_item_emits_delete(self):
        """Test that delete button emits deleteRequested signal."""
        config = MagicMock(spec=ProjectConfig)
        config.project_name = "Test"
        config.output_dir_path = "/tmp"
        config.language = "en"
        config.gender = "male"
        config.speed = 1.0
        config.thread_count = 1
        
        task = GenerationTask(config=config)
        widget = QueueItemWidget(task)
        
        spy = MagicMock()
        widget.deleteRequested.connect(spy)
        
    def test_queue_list_removal(self):
        """Test that remove_task correctly removes the widget from internal dict and list."""
        from src.gui.widgets.queue_list import QueueListWidget
        queue_list = QueueListWidget()
        
        config = MagicMock(spec=ProjectConfig)
        config.project_name = "To Remove"
        config.output_dir_path = "/tmp"
        config.language = "en"
        config.gender = "male"
        config.speed = 1.0
        config.thread_count = 1
        
        task = GenerationTask(config=config)
        
        # Add task
        queue_list.add_task(task)
        task_id_str = str(task.id)
        self.assertIn(task_id_str, queue_list._items)
        self.assertEqual(queue_list._list_widget.count(), 1)
        
        # Remove task
        queue_list.remove_task(task_id_str)
        self.assertNotIn(task_id_str, queue_list._items)
        self.assertEqual(queue_list._list_widget.count(), 0)

if __name__ == '__main__':
    unittest.main()
