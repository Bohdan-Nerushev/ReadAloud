import unittest
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

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

from src.domain.models import ProjectConfig, GenerationTask, TaskStatus, AudioChunk
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.infrastructure.file_manager import FileManager
from src.application.app_controller import ApplicationController
from src.application.services.queue_service import QueueService
from src.application.services.generation_service import GenerationService
from src.application.services.assembly_service import AssemblyService
from src.application.services.persistence_service import PersistenceService


class TestPersistenceIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.temp_dir, "state.json")
        self.persistence_service = PersistenceService(self.state_file)

        self.queue_service = QueueService()
        self.text_processor = TextProcessor()
        self.text_chunker = TextChunker()
        self.file_manager = FileManager()

        self.mock_generator = MagicMock()
        self.mock_generator._get_file_duration_fast.return_value = 2.5

        self.generation_service = GenerationService(self.mock_generator)
        self.assembly_service = AssemblyService(MagicMock())

        self.controller = ApplicationController(
            queue_service=self.queue_service,
            text_processor=self.text_processor,
            text_chunker=self.text_chunker,
            file_manager=self.file_manager,
            generation_service=self.generation_service,
            assembly_service=self.assembly_service,
            persistence_service=self.persistence_service
        )

        input_file = os.path.join(self.temp_dir, "input.txt")
        with open(input_file, "w", encoding="utf-8") as f:
            f.write("Some dummy test content.")

        self.config = ProjectConfig(
            project_name="IntegrationTest",
            input_file_path=input_file,
            language="en",
            gender="male",
            thread_count=2,
            output_dir_path=self.temp_dir,
            speed=1.0
        )


    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir)

    def test_queue_auto_save_and_restore(self) -> None:
        task1 = self.queue_service.add_task(self.config)
        self.assertTrue(os.path.exists(self.state_file))

        task2 = self.queue_service.add_task(self.config)
        self.assertTrue(os.path.exists(self.state_file))

        with open(self.state_file, "r", encoding="utf-8") as f:
            data = json_data = f.read()
            self.assertTrue(len(json_data) > 0)

        new_queue_service = QueueService()
        new_controller = ApplicationController(
            queue_service=new_queue_service,
            text_processor=self.text_processor,
            text_chunker=self.text_chunker,
            file_manager=self.file_manager,
            generation_service=self.generation_service,
            assembly_service=self.assembly_service,
            persistence_service=self.persistence_service
        )

        new_controller.restore_state()
        restored_tasks = new_queue_service.get_all_tasks()
        self.assertEqual(len(restored_tasks), 2)
        self.assertEqual(restored_tasks[0].id, task1.id)
        self.assertEqual(restored_tasks[1].id, task2.id)

    def test_progress_restoration_from_disk(self) -> None:

        text_dir = os.path.join(self.temp_dir, "text_chunks")
        audio_dir = os.path.join(self.temp_dir, "audio_chunks")
        os.makedirs(text_dir, exist_ok=True)
        os.makedirs(audio_dir, exist_ok=True)

        with open(os.path.join(text_dir, "1.txt"), "w", encoding="utf-8") as f:
            f.write("First chunk text content.")
        with open(os.path.join(text_dir, "2.txt"), "w", encoding="utf-8") as f:
            f.write("Second chunk text content.")

        audio_file1 = os.path.join(audio_dir, "1.mp3")
        with open(audio_file1, "w", encoding="utf-8") as f:
            f.write("dummy audio content")

        task = GenerationTask(config=self.config)
        task.text_dir = text_dir
        task.audio_dir = audio_dir
        task.status = TaskStatus.PROCESSING

        self.queue_service._current_task = task

        with patch.object(self.generation_service, 'start_generation') as mock_start_gen:
            success = self.controller._restore_progress_from_disk(task)
            self.assertTrue(success)

            self.assertEqual(len(self.controller._chunks), 2)
            self.assertEqual(self.controller._chunks[0].text_content, "First chunk text content.")
            self.assertEqual(self.controller._chunks[1].text_content, "Second chunk text content.")

            self.assertEqual(self.controller._audio_files[0], audio_file1)
            self.assertIsNone(self.controller._audio_files[1])

            self.assertEqual(self.controller._chunks[0].audio_file_path, audio_file1)
            self.assertEqual(self.controller._chunks[0].duration, 2.5)
            self.assertIsNone(self.controller._chunks[1].audio_file_path)

            mock_start_gen.assert_called_once_with(task, self.controller._chunks, audio_dir)

    def test_shutdown_saves_state_and_does_not_clean_temp(self) -> None:
        text_dir = os.path.join(self.temp_dir, "text_chunks")
        audio_dir = os.path.join(self.temp_dir, "audio_chunks")
        os.makedirs(text_dir, exist_ok=True)
        os.makedirs(audio_dir, exist_ok=True)

        task = self.queue_service.add_task(self.config)
        self.queue_service._task_queue.clear()
        self.queue_service._current_task = task
        task.text_dir = text_dir
        task.audio_dir = audio_dir
        task.status = TaskStatus.PROCESSING

        # Configure controller paths
        self.controller._text_dir = text_dir
        self.controller._audio_dir = audio_dir

        self.controller.shutdown()

        # Verify task marked as PAUSED
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertTrue(self.controller._is_stopped)

        # Verify state file exists and contains the task
        self.assertTrue(os.path.exists(self.state_file))
        restored = self.persistence_service.load_state()
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].id, task.id)
        self.assertEqual(restored[0].status, TaskStatus.PAUSED)

        # Verify directories are not deleted
        self.assertTrue(os.path.exists(text_dir))
        self.assertTrue(os.path.exists(audio_dir))


if __name__ == '__main__':
    unittest.main()
