import unittest
import os
import sys
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.domain.models import ProjectConfig, GenerationTask, TaskStatus
from src.application.services.persistence_service import PersistenceService


class TestPersistenceService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = self.temp_dir_obj.name

        self.state_file_path = os.path.join(self.temp_dir, "state.json")
        self.persistence_service = PersistenceService(self.state_file_path)

        self.input1 = os.path.join(self.temp_dir, "input1.txt")
        self.input2 = os.path.join(self.temp_dir, "input2.txt")
        Path(self.input1).write_text("dummy 1", encoding="utf-8")
        Path(self.input2).write_text("dummy 2", encoding="utf-8")

        self.config1 = ProjectConfig(
            project_name="Project1",
            input_file_path=self.input1,
            language="en",
            gender="male",
            thread_count=4,
            output_dir_path=self.temp_dir,
            speed=1.0
        )

        self.config2 = ProjectConfig(
            project_name="Project2",
            input_file_path=self.input2,
            language="uk",
            gender="female",
            thread_count=10,
            output_dir_path=self.temp_dir,
            speed=1.5
        )

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

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

    def test_missing_input_file_skips_task_and_prunes_state(self) -> None:
        """Verifies that tasks with deleted input files are skipped with warning and pruned from state."""
        input_file = os.path.join(self.temp_dir, "temp_story.txt")
        Path(input_file).write_text("Hello world", encoding="utf-8")

        config = ProjectConfig(
            project_name="TempProject",
            input_file_path=input_file,
            output_dir_path=self.temp_dir,
            language="en",
            gender="male",
            speed=1.0,
            thread_count=1
        )
        task = GenerationTask(config=config)
        self.persistence_service.save_state([task])
        self.assertTrue(os.path.exists(self.state_file_path))

        # Delete input file to simulate file removal/cleanup
        os.remove(input_file)

        # Loading state should skip the missing task and prune state
        restored = self.persistence_service.load_state()
        self.assertEqual(len(restored), 0)

        # Re-loading should return 0 tasks cleanly
        restored_after_prune = self.persistence_service.load_state()
        self.assertEqual(len(restored_after_prune), 0)


if __name__ == '__main__':
    unittest.main()
