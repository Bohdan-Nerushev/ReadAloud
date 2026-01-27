import unittest
import sys
from unittest.mock import MagicMock, patch, ANY

# Mock PyQt6
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

from src.application.services.generation_service import GenerationService
from src.domain.models import GenerationTask, AudioChunk, ProjectConfig

class TestGenerationService(unittest.TestCase):
    def setUp(self):
        self.mock_generator = MagicMock()
        self.mock_retry_handler = MagicMock()
        self.service = GenerationService(self.mock_generator, self.mock_retry_handler)
        
        # Mock Path validation again
        self.path_patcher = patch('src.domain.models.Path')
        self.mock_path = self.path_patcher.start()
        self.mock_path_instance = self.mock_path.return_value
        self.mock_path_instance.exists.return_value = True
        self.mock_path_instance.is_file.return_value = True
        self.mock_path_instance.is_dir.return_value = True

        self.config = ProjectConfig(
            project_name="GenTest",
            input_file_path="/mock/in.txt",
            language="en",
            gender="male",
            thread_count=4,
            output_dir_path="/mock/out"
        )
        self.task = GenerationTask(config=self.config)
        self.chunks = [AudioChunk(chunk_number=i+1, text_content=f"text {i}") for i in range(25)]

    def tearDown(self):
        self.path_patcher.stop()

    @patch('src.application.services.generation_service.ThreadManager')
    def test_batch_submission_logic(self, mock_thread_manager_class):
        """test_batch_submission_logic: Перевірка розбиття чанків на батчі."""
        mock_tm = mock_thread_manager_class.return_value
        self.service.start_generation(self.task, self.chunks, "/mock/out")
        
        # BATCH_GEN_SIZE is 10. 25 chunks -> 3 batches (10, 10, 5)
        # Verify submit_task was called 3 times
        self.assertEqual(mock_tm.submit_task.call_count, 3)

    def test_generation_retry_logic(self):
        """test_generation_retry_logic: Симуляція помилок та перевірка RetryHandler."""
        # This requires simulating the _generate_batch_safe execution
        batch = self.chunks[:10]
        self.service._output_dir = "/mock/out"
        
        # Setup mock retry handler to just return mock paths
        self.mock_retry_handler.execute_with_retry.return_value = [f"/mock/out/{i}.mp3" for i in range(10)]
        
        self.service._generate_batch_safe(batch, "en", "male", "test-id")
        
        # Verify it called retry handler
        self.mock_retry_handler.execute_with_retry.assert_called_once()

    @patch('src.application.services.generation_service.ThreadManager')
    def test_pause_resume_behavior(self, mock_thread_manager_class):
        """test_pause_resume_behavior: Перевірка зупинки та відновлення потоків."""
        mock_tm = mock_thread_manager_class.return_value
        self.service.start_generation(self.task, self.chunks, "/mock/out")
        
        # Test pause (when currently running)
        mock_tm.is_paused.return_value = False
        is_paused = self.service.pause()
        self.assertTrue(is_paused)
        mock_tm.pause.assert_called_once()
        
        # Test resume (when currently paused)
        mock_tm.is_paused.return_value = True
        is_paused = self.service.pause()
        self.assertFalse(is_paused)
        mock_tm.resume.assert_called_once()

if __name__ == '__main__':
    unittest.main()
