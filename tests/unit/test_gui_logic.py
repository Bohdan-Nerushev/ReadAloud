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
    def setUp(self):
        if app:
            app.processEvents()

    def tearDown(self):
        if app:
            app.processEvents()

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
        config.input_file_path = "/tmp/test.txt"
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
        config.input_file_path = "/tmp/test.txt"
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
        config.input_file_path = "/tmp/test.txt"
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

    def test_multiple_tasks_addition(self):
        """Test adding multiple tasks to QueueListWidget simultaneously."""
        import tempfile
        from src.gui.widgets.queue_list import QueueListWidget
        queue_list = QueueListWidget()

        tasks = []
        temp_files = []
        try:
            for i in range(5):
                tf = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
                tf.write(b"Hello world")
                tf.close()
                temp_files.append(tf.name)

                config = ProjectConfig(
                    project_name=f"Task_{i}",
                    input_file_path=tf.name,
                    output_dir_path="/tmp",
                    language="en",
                    gender="male",
                    speed=1.0,
                    thread_count=1
                )
                task = GenerationTask(config=config)
                tasks.append(task)
                queue_list.add_task(task)

            self.assertEqual(queue_list._list_widget.count(), 5)
            self.assertEqual(len(queue_list._items), 5)

            for task in tasks:
                queue_list.remove_task(str(task.id))
            self.assertEqual(queue_list._list_widget.count(), 0)
        finally:
            for p in temp_files:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass

    def test_progress_display_robustness(self):
        """Test ProgressDisplayWidget with various inputs without crashing."""
        from src.gui.widgets.progress_display import ProgressDisplayWidget
        progress_display = ProgressDisplayWidget()
        progress_display.update_progress(10, 100, "01:00", 2.5, task_name="Test", file_path="/tmp/test.txt")
        progress_display.update_progress(0, 0, "00:00", None, task_name=None, file_path=None)
        progress_display.reset()
        progress_display.set_complete()

if __name__ == '__main__':
    unittest.main()
