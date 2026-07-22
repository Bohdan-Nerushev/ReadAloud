import sys
import unittest
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication
from src.gui.styles import Styles, Palette
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
        
        # Running state
        widget.set_running_state()
        self.assertTrue(widget._start_button.isEnabled())
        
        # Paused state
        widget.set_paused_state()
        self.assertTrue(widget._start_button.isEnabled())
        
        # Disable all
        widget.disable_all()
        self.assertFalse(widget._start_button.isEnabled())
        
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
        widget.delete_button.click()
        spy.assert_called_once_with(str(task.id))

    def test_queue_item_emits_pause(self):
        """Test that pause button emits pauseRequested signal."""
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
        widget.pauseRequested.connect(spy)
        widget.pause_button.click()
        spy.assert_called_once_with(str(task.id))
        
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
