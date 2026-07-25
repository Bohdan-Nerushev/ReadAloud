import unittest
import tempfile
import shutil
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.domain.audio_assembler import AudioAssembler
from src.infrastructure.file_manager import FileManager
from src.infrastructure.network_manager import NetworkManager
from src.domain.text_chunker import TextChunker
from src.domain.text_processor import TextProcessor
from src.application.services.queue_service import QueueService
from src.domain.models import ProjectConfig, GenerationTask, TaskStatus, AudioChunk


class TestRobustnessEdgeCases(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    # ------------------------------------------------------------------
    # AudioAssembler Edge Cases
    # ------------------------------------------------------------------

    def test_audio_assembler_empty_file_list_raises_value_error(self):
        """Verifies assemble_audio raises ValueError when given an empty list of files."""
        assembler = AudioAssembler()
        with self.assertRaises(ValueError):
            assembler.assemble_audio([], str(Path(self.temp_dir) / "out.mp3"))

    def test_audio_assembler_zero_byte_chunk_raises_value_error(self):
        """Verifies assemble_audio raises ValueError when an audio chunk is empty (0 bytes)."""
        assembler = AudioAssembler()
        empty_chunk = Path(self.temp_dir) / "empty.mp3"
        empty_chunk.write_bytes(b"")

        output_path = Path(self.temp_dir) / "output.mp3"
        with self.assertRaises(Exception) as ctx:
            assembler.assemble_audio([str(empty_chunk)], str(output_path))
        self.assertIn("0 bytes", str(ctx.exception))

    def test_audio_assembler_missing_chunk_raises_value_error(self):
        """Verifies assemble_audio raises ValueError when a chunk path does not exist."""
        assembler = AudioAssembler()
        missing_chunk = Path(self.temp_dir) / "nonexistent.mp3"
        output_path = Path(self.temp_dir) / "output.mp3"

        with self.assertRaises(Exception) as ctx:
            assembler.assemble_audio([str(missing_chunk)], str(output_path))
        self.assertIn("does not exist", str(ctx.exception))

    def test_audio_assembler_negative_speed_raises_value_error(self):
        """Verifies assemble_audio raises ValueError when speed <= 0."""
        assembler = AudioAssembler()
        chunk = Path(self.temp_dir) / "valid.mp3"
        chunk.write_bytes(b"dummy audio data")
        output_path = Path(self.temp_dir) / "output.mp3"

        with self.assertRaises(Exception) as ctx:
            assembler.assemble_audio([str(chunk)], str(output_path), speed=0.0)
        self.assertIn("Speed must be positive", str(ctx.exception))

    # ------------------------------------------------------------------
    # FileManager Edge Cases
    # ------------------------------------------------------------------

    def test_file_manager_cleanup_temp_directories_handles_invalid_elements(self):
        """Verifies cleanup_temp_directories safely skips None, empty, or non-string elements."""
        fm = FileManager()
        # Should not raise exception
        fm.cleanup_temp_directories([None, "", "   ", 12345])

    def test_file_manager_create_timestamped_dir_empty_base_raises(self):
        """Verifies create_timestamped_dir raises ValueError if base_name is empty."""
        fm = FileManager()
        with self.assertRaises(ValueError):
            fm.create_timestamped_dir("", self.temp_dir)

    def test_file_manager_create_timestamped_dir_nonexistent_parent_raises(self):
        """Verifies create_timestamped_dir raises ValueError if parent_dir does not exist."""
        fm = FileManager()
        with self.assertRaises(ValueError):
            fm.create_timestamped_dir("audio", str(Path(self.temp_dir) / "nonexistent_dir"))

    def test_file_manager_cleanup_temp_directories_handles_nonexistent(self):
        """Verifies cleanup_temp_directories cleanly skips nonexistent directories without crashing."""
        fm = FileManager()
        nonexistent = str(Path(self.temp_dir) / "ghost_folder")
        # Should not raise exception
        fm.cleanup_temp_directories([nonexistent])

    # ------------------------------------------------------------------
    # NetworkManager Edge Cases
    # ------------------------------------------------------------------

    @patch("socket.create_connection")
    def test_network_manager_is_connected_retries_all_targets(self, mock_conn):
        """Verifies NetworkManager tries secondary endpoints if primary fails."""
        mock_conn.side_effect = [OSError("Connection failed"), MagicMock()]
        nm = NetworkManager(test_targets=[("1.1.1.1", 53), ("8.8.8.8", 53)])
        self.assertTrue(nm.is_connected(timeout=0.5))
        self.assertEqual(mock_conn.call_count, 2)

    # ------------------------------------------------------------------
    # TextChunker Edge Cases
    # ------------------------------------------------------------------

    def test_text_chunker_invalid_chunk_size_raises(self):
        """Verifies chunk_text raises ValueError when chunk_size <= 0."""
        chunker = TextChunker()
        with self.assertRaises(ValueError):
            chunker.chunk_text("Hello world", chunk_size=0)
        with self.assertRaises(ValueError):
            chunker.chunk_text("Hello world", chunk_size=-10)

    def test_text_chunker_extremely_long_unbroken_string(self):
        """Verifies chunk_text correctly handles strings without spaces that exceed chunk_size."""
        chunker = TextChunker()
        long_word = "A" * 50
        chunks = chunker.chunk_text(long_word, chunk_size=10)
        self.assertGreater(len(chunks), 0)
        self.assertEqual(chunks[0].chunk_number, 1)

    def test_text_chunker_unbroken_string_capped_at_2x_chunk_size(self):
        """Verifies chunk length is capped at 2x chunk_size when no delimiter is found."""
        chunker = TextChunker()
        unbroken = "X" * 100
        chunks = chunker.chunk_text(unbroken, chunk_size=10)
        # Each chunk should not exceed 2 * chunk_size = 20 chars
        for c in chunks:
            self.assertLessEqual(len(c.text_content), 20)

    # ------------------------------------------------------------------
    # QueueService Edge Cases
    # ------------------------------------------------------------------

    def test_queue_service_toggle_pause_nonexistent_task(self):
        """Verifies toggle_task_pause returns None for invalid task IDs."""
        qs = QueueService()
        result = qs.toggle_task_pause("nonexistent-uuid-12345")
        self.assertIsNone(result)

    def test_queue_service_remove_nonexistent_task(self):
        """Verifies remove_task returns False when given a non-existent task ID."""
        qs = QueueService()
        result = qs.remove_task("nonexistent-uuid-12345")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
