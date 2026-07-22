import unittest
import sys
import threading
import asyncio
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock edge_tts before import
sys.modules['edge_tts'] = MagicMock()

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.domain.models import AudioChunk, ProjectConfig, GenerationTask
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.application.app_controller import ApplicationController


class TestOptimization(unittest.TestCase):

    def test_audio_generator_loop_reuse(self):
        generator = AudioGenerator()
        
        # Check if loop is initialized and running
        loop = generator._loop
        self.assertTrue(isinstance(loop, asyncio.AbstractEventLoop), "Should be an asyncio loop")
        self.assertTrue(loop.is_running(), "Event loop should be running in the background thread")

    def test_audio_assembler_copy_codec(self):
        assembler = AudioAssembler()
        
        with patch('subprocess.Popen') as mock_popen, \
             patch('src.domain.audio_assembler.Path') as mock_path_cls, \
             patch('builtins.open', new_callable=MagicMock) as mock_file_open:
            
            # Configure Path mock
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.parent.exists.return_value = True
            mock_path_instance.resolve.return_value.as_posix.return_value = "/tmp/safe/path"
            mock_path_cls.return_value = mock_path_instance
            
            # Configure open mock
            mock_file_handle = MagicMock()
            mock_file_open.return_value.__enter__.return_value = mock_file_handle
            
            mock_process = MagicMock()
            mock_process.stderr.readline.return_value = b""
            mock_process.poll.return_value = 0
            mock_process.returncode = 0
            mock_popen.return_value = mock_process
            
            # Test 1: copy_codec=True
            assembler.assemble_audio(['1.mp3', '2.mp3'], 'out.mp3', copy_codec=True)
            
            args, _ = mock_popen.call_args
            cmd = args[0]
            self.assertTrue('-c' in cmd and 'copy' in cmd, "Should use -c copy")
            self.assertTrue('tempol=' not in str(cmd) and 'filter:a' not in str(cmd), "Should NOT use filters")
            
            # Test 2: copy_codec=False (re-encode)
            assembler.assemble_audio(['1.mp3', '2.mp3'], 'out.mp3', copy_codec=False, speed=1.5)
            args, _ = mock_popen.call_args
            cmd = args[0]
            self.assertTrue('atempo=1.5' in str(cmd) or 'filter:a' in str(cmd), "Should use filters")

    def test_app_controller_batch_logic(self):
        with patch('src.domain.audio_assembler.Path') as mock_path_cls, \
             patch('src.domain.models.Path') as mock_models_path:
            
            # Configure Path mocks
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.parent.exists.return_value = True
            mock_path_instance.is_dir.return_value = True
            mock_path_instance.resolve.return_value.as_posix.return_value = "/tmp/safe/path"
            
            mock_path_cls.return_value = mock_path_instance
            mock_models_path.return_value = mock_path_instance
            
            # Check logic
            controller = ApplicationController(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock()
            )
            
            # Mock services
            controller._audio_generator = MagicMock()
            controller._audio_assembler = MagicMock()
            controller._file_manager = MagicMock()
            controller._text_processor = MagicMock()
            controller._text_chunker = MagicMock()
            
            # Mock Config
            config = ProjectConfig(
                project_name="test",
                input_file_path="in.txt",
                language="en",
                gender="male",
                thread_count=1,
                output_dir_path="out",
                speed=1.0
            )
            
            # Prepare Controller
            # Simulate Chunks
            chunks = [AudioChunk(i+1, "text") for i in range(5)]
            
            # Prepare Controller
            task = GenerationTask(config=config)
            controller._queue_service.get_current_task.return_value = task
            controller._chunks = chunks
            controller._audio_files = [None] * len(chunks)
            controller.BATCH_SIZE = 2
            controller._assembly_service.BATCH_SIZE = 2
            controller._assembly_service.reset(len(chunks))
            
            # Case 1: 1 chunk ready -> mark_chunk_ready returns None, no submit
            controller._assembly_service.mark_chunk_ready.return_value = None
            controller._on_chunk_generated(1, "1.mp3", 1.5)
            
            self.assertEqual(controller._audio_files[0], "1.mp3")
            controller._assembly_service.submit_batch_by_index.assert_not_called()
            
            # Case 2: Batch complete -> mark_chunk_ready returns batch_index, submit is called
            controller._assembly_service.mark_chunk_ready.return_value = 0
            controller._audio_dir = "/tmp/audio_dir"
            controller._on_chunk_generated(2, "2.mp3", 1.5)
            
            self.assertEqual(controller._audio_files[1], "2.mp3")
            controller._assembly_service.submit_batch_by_index.assert_called_once_with(
                0,
                ["1.mp3", "2.mp3"],
                "/tmp/audio_dir",
                1.0,
                str(task.id)
            )


if __name__ == "__main__":
    unittest.main()
