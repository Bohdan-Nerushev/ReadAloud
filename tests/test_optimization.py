
import sys
import threading
import asyncio
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Mock edge_tts before import
sys.modules['edge_tts'] = MagicMock()

# Mock PyQt6
mock_qt_core = MagicMock()
class MockQObject:
    def __init__(self, *args, **kwargs): pass

class MockQThread(MockQObject):
    finished = MagicMock()
    error = MagicMock()
    def start(self): pass
    def terminate(self): pass
    def wait(self): pass
    def isRunning(self): return False

class MockQTimer(MockQObject):
    timeout = MagicMock()
    def start(self, *args): pass
    def stop(self): pass

def mock_signal(*args):
    s = MagicMock()
    s.connect = MagicMock()
    s.emit = MagicMock()
    return s

mock_qt_core.QObject = MockQObject
mock_qt_core.QThread = MockQThread
mock_qt_core.QTimer = MockQTimer
mock_qt_core.pyqtSignal = mock_signal

sys.modules['PyQt6.QtCore'] = mock_qt_core
sys.modules['PyQt6.QtWidgets'] = MagicMock()
sys.modules['PyQt6'] = MagicMock()

from src.domain.models import AudioChunk, ProjectConfig
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.application.app_controller import ApplicationController

def test_audio_generator_loop_reuse():
    print("Testing AudioGenerator Event Loop Reuse...")
    generator = AudioGenerator()
    
    # Check if loop is reused in same thread
    loop1 = generator._get_or_create_loop()
    loop2 = generator._get_or_create_loop()
    
    assert loop1 is loop2, "Event loop should be reused within same thread"
    assert isinstance(loop1, asyncio.AbstractEventLoop), "Should be an asyncio loop"
    
    # Check different thread has different loop
    result_container = {}
    def check_other_thread():
        loop3 = generator._get_or_create_loop()
        result_container['loop3'] = loop3
        result_container['is_different'] = loop3 is not loop1
        
    t = threading.Thread(target=check_other_thread)
    t.start()
    t.join()
    
    assert result_container['is_different'], "Different threads should have different loops"
    print("✓ AudioGenerator Loop Reuse verified")

def test_audio_assembler_copy_codec():
    print("Testing AudioAssembler copy_codec support...")
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
        assert '-c' in cmd and 'copy' in cmd, "Should use -c copy"
        assert 'tempol=' not in str(cmd) and 'filter:a' not in str(cmd), "Should NOT use filters"
        
        # Test 2: copy_codec=False (re-encode)
        assembler.assemble_audio(['1.mp3', '2.mp3'], 'out.mp3', copy_codec=False, speed=1.5)
        args, _ = mock_popen.call_args
        cmd = args[0]
        assert 'atempo=1.5' in str(cmd) or 'filter:a' in str(cmd), "Should use filters"
        
    print("✓ AudioAssembler copy_codec verified")

def test_app_controller_batch_logic():
    print("Testing AppController Batch Logic...")
    
    with patch('src.domain.audio_assembler.Path') as mock_path_cls:
        # Check logic
        controller = ApplicationController()
        
        # Mock services
        controller._audio_generator = MagicMock()
        controller._audio_assembler = MagicMock()
        controller._file_manager = MagicMock()
        controller._text_processor = MagicMock()
        controller._text_chunker = MagicMock()
        
        # Mock Config
        config = ProjectConfig("test", "in.txt", "en", "male", 1.0, 1, "out")
        
        # Prepare Controller
        controller.start_generation(config)
        controller.BATCH_SIZE = 2
        
        # Simulate Chunks
        chunks = [AudioChunk(i+1, "text") for i in range(5)]
        controller._on_preparation_finished(chunks, "text_dir", "audio_dir")
        
        # Case 1: 1 chunk ready -> No batch
        controller._audio_files[0] = "1.mp3"
        controller._check_batch_assembly()
        assert len(controller._batch_submitted) == 0, "Should wait for full batch"
        
        # Case 2: 2 chunks ready -> Trigger Batch 0
        with patch.object(controller, '_trigger_batch_assembly', wraps=controller._trigger_batch_assembly) as mock_trigger:
             # We need to mock executor submit otherwise it runs real code which might fail on path 
             # But we want to test _trigger_batch_assembly calling _audio_assembler
             # Let's mock _assembly_executor.submit
             controller._assembly_executor = MagicMock()
             
             controller._audio_files[1] = "2.mp3"
             controller._check_batch_assembly()
             
             assert 0 in controller._batch_submitted, "Batch 0 should be submitted"
             
             # Verify executor call
             assert controller._assembly_executor.submit.called
             
    print("✓ AppController Batch Logic verified")

if __name__ == "__main__":
    test_audio_generator_loop_reuse()
    test_audio_assembler_copy_codec()
    test_app_controller_batch_logic()
    print("\nALL VERIFICATION TESTS PASSED")
