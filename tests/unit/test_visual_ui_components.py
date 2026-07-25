"""
Visual UI Components Test Suite.

This module provides thorough test coverage for visual GUI components, layout elements,
button states, progress display updates, and queue list interactions.
"""

import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSize, Qt

# Initialize QApplication once for GUI tests
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.gui.styles import Styles, Palette
from src.gui.widgets.progress_display import ProgressDisplayWidget
from src.gui.widgets.queue_item import QueueItemWidget
from src.gui.widgets.queue_list import QueueListWidget
from src.gui.widgets.file_selector import FileSelectorWidget
from src.domain.models import ProjectConfig, GenerationTask, TaskStatus


class TestVisualUIComponents(unittest.TestCase):
    """Unit tests for visual GUI widgets and layout elements."""

    def setUp(self) -> None:
        if app:
            app.processEvents()

    def tearDown(self) -> None:
        if app:
            app.processEvents()

    # ------------------------------------------------------------------
    # ProgressDisplayWidget Tests
    # ------------------------------------------------------------------

    def test_progress_display_widget_layout_and_updates(self) -> None:
        """Verifies ProgressDisplayWidget initializes labels and updates progress correctly."""
        widget = ProgressDisplayWidget()
        self.assertEqual(widget.height(), Styles.PROGRESS_WIDGET_HEIGHT)

        # Update progress with task_name and file_path
        widget.update_progress(
            completed=15,
            total=30,
            eta="00:05:30",
            speed=2.5,
            task_name="MyProject",
            file_path="/tmp/book.txt"
        )
        self.assertEqual(widget._progress_bar.value(), 50)
        self.assertIn("Active File: /tmp/book.txt", widget._file_label.text())
        self.assertIn("Progress: 15/30 completed", widget._status_label.text())
        self.assertIn("Speed: 2.5 ch/s", widget._status_label.text())
        self.assertIn("ETA: 00:05:30", widget._status_label.text())

    def test_progress_display_widget_assembly_and_global_progress(self) -> None:
        """Verifies assembly phase progress and global queue progress updates."""
        widget = ProgressDisplayWidget()

        # Assembly progress update
        widget.update_assembly_progress(percentage=75.5, remaining_seconds=95.0)
        self.assertEqual(widget._progress_bar.value(), 75)
        self.assertIn("Assembling audio... 75.5%", widget._status_label.text())
        self.assertIn("ETA: 01:35", widget._status_label.text())

        # Global queue progress update
        widget.update_global_progress(percentage=45.0, eta="02:15:00")
        self.assertIn("Global Queue Progress: 45%", widget._global_status_label.text())
        self.assertIn("Total ETA: 02:15:00", widget._global_status_label.text())

        # Complete and Reset
        widget.set_complete()
        self.assertEqual(widget._progress_bar.value(), 100)
        self.assertEqual(widget._status_label.text(), "Generation complete!")

        widget.reset()
        self.assertEqual(widget._progress_bar.value(), 0)
        self.assertEqual(widget._file_label.text(), "")

    # ------------------------------------------------------------------
    # QueueItemWidget Tests
    # ------------------------------------------------------------------

    def test_queue_item_widget_rendering(self) -> None:
        """Verifies QueueItemWidget renders task metadata and size hints correctly."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tf.write(b"Sample text file content for duration and size testing.")
            tf_path = tf.name

        try:
            config = ProjectConfig(
                project_name="TestBook",
                input_file_path=tf_path,
                output_dir_path="/tmp",
                language="uk",
                gender="female",
                speed=1.2,
                thread_count=4
            )
            task = GenerationTask(config=config)
            item_widget = QueueItemWidget(task)

            # Check size hint and fixed height
            hint = item_widget.sizeHint()
            if hasattr(hint, 'height') and callable(hint.height):
                val = hint.height()
                if isinstance(val, int):
                    self.assertEqual(val, Styles.QUEUE_ITEM_HEIGHT)
            self.assertIn("TestBook", item_widget.name_label.text())
            self.assertIn("Pending", item_widget.status_label.text())

            # Test signals
            delete_spy = MagicMock()
            pause_spy = MagicMock()
            item_widget.deleteRequested.connect(delete_spy)
            item_widget.pauseRequested.connect(pause_spy)

            item_widget.delete_button.click()
            delete_spy.assert_called_once_with(str(task.id))

            item_widget.pause_button.click()
            pause_spy.assert_called_once_with(str(task.id))

        finally:
            Path(tf_path).unlink(missing_ok=True)

    def test_queue_item_widget_file_size_and_est_duration(self) -> None:
        """Verifies QueueItemWidget helper methods _get_file_size_str and _get_est_duration_str."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tf.write(b"A" * 3000)  # 3000 characters (~200 seconds speech)
            tf_path = tf.name

        try:
            config = ProjectConfig(
                project_name="Dummy",
                input_file_path=tf_path,
                output_dir_path="/tmp",
                language="en",
                gender="male",
                speed=1.0,
                thread_count=1
            )
            task = GenerationTask(config=config)
            item_widget = QueueItemWidget(task)

            size_str = item_widget._get_file_size_str(tf_path)
            self.assertIn("KB", size_str)

            dur_str = item_widget._get_est_duration_str(tf_path, speed=1.0)
            self.assertTrue("m" in dur_str or "s" in dur_str)

            # Non-existent file fallback
            self.assertEqual(item_widget._get_file_size_str("/non/existent/path.txt"), "N/A")
            self.assertEqual(item_widget._get_est_duration_str("/non/existent/path.txt", 1.0), "N/A")
        finally:
            Path(tf_path).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # QueueListWidget Tests
    # ------------------------------------------------------------------

    def test_queue_list_widget_multiple_tasks(self) -> None:
        """Verifies QueueListWidget manages adding, updating, and removing multiple tasks."""
        queue_list = QueueListWidget()
        temp_paths = []

        try:
            tasks = []
            for i in range(3):
                tf = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
                tf.write(b"Sample text for task " + str(i).encode('utf-8'))
                tf.close()
                temp_paths.append(tf.name)

                config = ProjectConfig(
                    project_name=f"QueueTask_{i}",
                    input_file_path=tf.name,
                    output_dir_path="/tmp",
                    language="uk",
                    gender="male",
                    speed=1.0,
                    thread_count=2
                )
                task = GenerationTask(config=config)
                tasks.append(task)
                queue_list.add_task(task)

            self.assertEqual(queue_list._list_widget.count(), 3)
            self.assertEqual(len(queue_list._items), 3)

            # Update task
            updated_task = tasks[0]
            updated_task.update_status(TaskStatus.PROCESSING, "Processing chunk 5/10")
            updated_task.update_progress(50.0)
            queue_list.update_task(updated_task)

            # Remove tasks one by one
            for task in tasks:
                queue_list.remove_task(str(task.id))

            self.assertEqual(queue_list._list_widget.count(), 0)
            self.assertEqual(len(queue_list._items), 0)

        finally:
            for p in temp_paths:
                Path(p).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # FileSelectorWidget Tests
    # ------------------------------------------------------------------

    def test_file_selector_widget_selection_and_clear(self) -> None:
        """Verifies FileSelectorWidget signal emissions, file path stored, and clear state."""
        widget = FileSelectorWidget()

        self.assertEqual(widget.get_selected_files(), [])
        self.assertEqual(widget.get_selected_file(), "")

        # Simulate selection of single file
        widget._selected_files = ["/tmp/sample.txt"]
        widget._file_label.setText("sample.txt")

        self.assertEqual(widget.get_selected_files(), ["/tmp/sample.txt"])
        self.assertEqual(widget.get_selected_file(), "/tmp/sample.txt")

        # Clear selection
        widget.clear()
        self.assertEqual(widget.get_selected_files(), [])
        self.assertEqual(widget.get_selected_file(), "")
        self.assertIn("No file selected", widget._file_label.text())


if __name__ == "__main__":
    unittest.main()
