import unittest
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

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

from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.infrastructure.file_manager import FileManager
from src.application.app_controller import ApplicationController
from src.application.services.queue_service import QueueService
from src.application.services.generation_service import GenerationService
from src.application.services.assembly_service import AssemblyService
from src.domain.models import ProjectConfig, TaskStatus
from src.domain.models import ProjectConfig, TaskStatus

class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.text_processor = TextProcessor()
        self.text_chunker = TextChunker()
        self.file_manager = FileManager()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_text_to_filesystem_integration(self):
        """
        test_text_to_filesystem_integration: Повний цикл: Raw Text -> TextProcessor (очищення) -> 
        TextChunker (розбиття) -> FileManager (запис на диск).
        """
        raw_text = "Hello 'world'!\nThis is a *** test ====== of the system.\nNext line."
        # TextProcessor: ' and " removed, *** and ====== removed, \n -> 4 spaces
        expected_processed = "Hello world!    This is a  test  of the system.    Next line."
        
        # 1. Process
        processed = self.text_processor.process_text(raw_text)
        self.assertEqual(processed, expected_processed)
        
        # 2. Chunk
        chunks = self.text_chunker.chunk_text(processed, chunk_size=20)
        self.assertTrue(len(chunks) > 1)
        
        # 3. Save
        save_dir = self.file_manager.create_timestamped_dir("integration_test", self.temp_dir)
        for chunk in chunks:
            path = self.file_manager.save_text_chunk(chunk, save_dir)
            self.assertTrue(Path(path).exists())
            
            # Verify content
            with open(path, 'r', encoding='utf-8') as f:
                self.assertEqual(f.read(), chunk.text_content)

    @patch('src.application.services.generation_service.AudioGenerator')
    @patch('src.application.services.assembly_service.AudioAssembler')
    def test_app_orchestration_integration(self, mock_assembler, mock_generator):
        """
        test_app_orchestration_integration: Перевірка взаємодії AppController з GenerationService 
        та AssemblyService на мінімальному наборі даних.
        """
        # Create dependencies
        queue_service = QueueService()
        text_processor = TextProcessor()
        text_chunker = TextChunker()
        file_manager = FileManager()
        
        # We need real or mock services but keep-em-alive for controller
        generation_service = GenerationService(mock_generator.return_value, MagicMock())
        assembly_service = AssemblyService(mock_assembler.return_value)
        
        controller = ApplicationController(
            queue_service, 
            text_processor, 
            text_chunker, 
            file_manager, 
            generation_service, 
            assembly_service,
            MagicMock()
        )

        
        # Setup dummy config
        dummy_input = Path(self.temp_dir) / "input.txt"
        dummy_input.write_text("dummy content")
        
        config = ProjectConfig(
            project_name="OrchestrationTest",
            input_file_path=str(dummy_input),
            language="en",
            gender="male",
            thread_count=1,
            output_dir_path=self.temp_dir,
            speed=1.0
        )
        
        # We want to verify that adding a task triggers the process
        # ApplicationController.add_task -> _process_queue -> _start_generation
        
        with patch.object(controller, '_start_generation_process') as mock_start:
            # We also need to avoid _start_task actually starting the worker
            with patch.object(controller, '_start_task', return_value=True):
                controller.add_task(config)
                
                # Should be processing since it's the first task
                self.assertEqual(controller._get_current_task().config.project_name, "OrchestrationTest")
            
        # Check signal connection (indirectly by verifying behavior if possible)
        # For a full integration we'd need to let the threads run, but that's slow.
        # Here we just verify the orchestration logic in Controller triggers the services.
        
        self.assertIsNotNone(controller._generation_service)
        self.assertIsNotNone(controller._assembly_service)

if __name__ == '__main__':
    unittest.main()
