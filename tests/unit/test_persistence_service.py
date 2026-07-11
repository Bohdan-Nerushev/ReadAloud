import unittest
import os
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# Mock PyQt6
import sys
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

from src.domain.models import ProjectConfig, GenerationTask, TaskStatus
from src.application.services.persistence_service import PersistenceService


class TestPersistenceService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.temp_file.close()
        self.state_file_path = self.temp_file.name
        self.persistence_service = PersistenceService(self.state_file_path)

        self.path_patcher = patch('src.domain.models.Path')
        self.mock_path = self.path_patcher.start()
        self.mock_path_instance = self.mock_path.return_value
        self.mock_path_instance.exists.return_value = True
        self.mock_path_instance.is_file.return_value = True
        self.mock_path_instance.is_dir.return_value = True

        self.config1 = ProjectConfig(
            project_name="Project1",
            input_file_path="/mock/input1.txt",
            language="en",
            gender="male",
            thread_count=4,
            output_dir_path="/mock/output1",
            speed=1.0
        )

        self.config2 = ProjectConfig(
            project_name="Project2",
            input_file_path="/mock/input2.txt",
            language="uk",
            gender="female",
            thread_count=10,
            output_dir_path="/mock/output2",
            speed=1.5
        )

    def tearDown(self) -> None:
        self.path_patcher.stop()
        if os.path.exists(self.state_file_path):
            os.unlink(self.state_file_path)

    def test_save_and_load_empty_queue(self) -> None:
        success = self.persistence_service.save_state([])
        self.assertTrue(success)

        tasks = self.persistence_service.load_state()
        self.assertEqual(len(tasks), 0)

    def test_save_and_load_multiple_tasks(self) -> None:
        task1 = GenerationTask(config=self.config1)
        task2 = GenerationTask(config=self.config2)

        task1.update_status(TaskStatus.PROCESSING, "Generating chunks")
        task1.update_progress(45.5)

        task2.update_status(TaskStatus.PENDING, "Waiting in queue")

        success = self.persistence_service.save_state([task1, task2])
        self.assertTrue(success)

        restored_tasks = self.persistence_service.load_state()
        self.assertEqual(len(restored_tasks), 2)

        self.assertEqual(restored_tasks[0].id, task1.id)
        self.assertEqual(restored_tasks[0].config.project_name, "Project1")
        self.assertEqual(restored_tasks[0].status, TaskStatus.PROCESSING)
        self.assertEqual(restored_tasks[0].progress, 45.5)
        self.assertEqual(restored_tasks[0].message, "Generating chunks")

        self.assertEqual(restored_tasks[1].id, task2.id)
        self.assertEqual(restored_tasks[1].config.project_name, "Project2")
        self.assertEqual(restored_tasks[1].status, TaskStatus.PENDING)
        self.assertEqual(restored_tasks[1].progress, 0.0)

    def test_save_and_load_task_with_dirs(self) -> None:
        task = GenerationTask(config=self.config1)
        task.text_dir = "/mock/text_dir_path"
        task.audio_dir = "/mock/audio_dir_path"
        task.update_status(TaskStatus.PAUSED, "Paused execution")
        task.update_progress(75.0)

        success = self.persistence_service.save_state([task])
        self.assertTrue(success)

        restored_tasks = self.persistence_service.load_state()
        self.assertEqual(len(restored_tasks), 1)

        restored = restored_tasks[0]
        self.assertEqual(restored.id, task.id)
        self.assertEqual(restored.text_dir, "/mock/text_dir_path")
        self.assertEqual(restored.audio_dir, "/mock/audio_dir_path")
        self.assertEqual(restored.status, TaskStatus.PAUSED)
        self.assertEqual(restored.progress, 75.0)

    def test_missing_file_returns_empty(self) -> None:
        non_existent_file = "/tmp/non_existent_readaloud_state_file.json"
        service = PersistenceService(non_existent_file)
        restored = service.load_state()
        self.assertEqual(restored, [])

    def test_corrupted_file_returns_empty_and_backups_it(self) -> None:
        with open(self.state_file_path, "w", encoding="utf-8") as f:
            f.write("{invalid json file content")

        restored = self.persistence_service.load_state()
        self.assertEqual(restored, [])
        self.assertFalse(os.path.exists(self.state_file_path))


if __name__ == '__main__':
    unittest.main()
