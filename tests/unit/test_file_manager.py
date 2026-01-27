import unittest
import sys
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.infrastructure.file_manager import FileManager
from src.domain.models import AudioChunk

class TestFileManager(unittest.TestCase):
    def setUp(self):
        self.file_manager = FileManager()

    @patch('src.infrastructure.file_manager.datetime')
    @patch('src.infrastructure.file_manager.Path.mkdir')
    @patch('src.infrastructure.file_manager.Path.exists', return_value=True)
    def test_create_timestamped_dir(self, mock_exists, mock_mkdir, mock_datetime):
        """test_create_timestamped_dir: Перевіряє створення папки з коректним форматом імені."""
        mock_datetime.now.return_value.strftime.return_value = "20231226_171530"
        
        result = self.file_manager.create_timestamped_dir("text", "/tmp")
        
        self.assertIn("20231226_171530_text", result)
        mock_mkdir.assert_called()

    @patch('builtins.open', new_callable=mock_open)
    @patch('src.infrastructure.file_manager.Path.exists', return_value=True)
    def test_save_text_chunk_writes_content(self, mock_exists, mocked_file):
        """test_save_text_chunk_writes_content: Перевіряє запис чанка у файл."""
        chunk = AudioChunk(chunk_number=5, text_content="Chunk content")
        
        self.file_manager.save_text_chunk(chunk, "/tmp/dir")
        
        # Verify file opened with correct name
        mocked_file.assert_called_with(unittest.mock.ANY, 'w', encoding='utf-8', buffering=unittest.mock.ANY)
        # Verify content written
        mocked_file().write.assert_called_once_with("Chunk content")

    @patch('shutil.rmtree')
    @patch('src.infrastructure.file_manager.Path.exists', return_value=True)
    @patch('src.infrastructure.file_manager.Path.is_dir', return_value=True)
    def test_cleanup_temp_directories(self, mock_is_dir, mock_exists, mock_rmtree):
        """test_cleanup_temp_directories: Перевіряє видалення списку директорій."""
        dirs = ["/tmp/1", "/tmp/2"]
        self.file_manager.cleanup_temp_directories(dirs)
        
        self.assertEqual(mock_rmtree.call_count, 2)

    @patch('src.infrastructure.file_manager.Path.mkdir')
    def test_ensure_directory_exists(self, mock_mkdir):
        """test_ensure_directory_exists: Перевіряє рекурсивне створення папок."""
        self.file_manager.ensure_directory_exists("/tmp/nested/dir")
        mock_mkdir.assert_called_with(parents=True, exist_ok=True)

    @patch('src.infrastructure.file_manager.Path.exists', return_value=False)
    def test_save_text_chunk_missing_dir(self, mock_exists):
        """test_save_text_chunk_missing_dir: Перевіряє помилку при неіснуючій папці."""
        chunk = AudioChunk(chunk_number=1, text_content="test")
        with self.assertRaises(ValueError) as cm:
            self.file_manager.save_text_chunk(chunk, "/nonexistent")
        self.assertIn("Directory does not exist", str(cm.exception))

if __name__ == '__main__':
    unittest.main()
