import unittest
import sys
import tempfile
import shutil
from unittest.mock import MagicMock, patch
from pathlib import Path

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

from src.application.services.assembly_service import AssemblyService
from src.domain.models import AudioChunk

class TestAssemblyService(unittest.TestCase):
    def setUp(self):
        self.mock_assembler = MagicMock()
        self.service = AssemblyService(self.mock_assembler)

    def test_batch_completion_tracking(self):
        """test_batch_completion_tracking: Перевірка логіки очікування всіх частин."""
        # Setup: 110 chunks, BATCH_SIZE=40 -> 3 batches (40, 40, 30)
        self.service.reset(total_chunks=110)
        
        # 1. Fill some files but not enough for a full batch (30 files, batch size is 40)
        for i in range(30):
            self.service.mark_chunk_ready(i, duration=1.0)
            
        available_files = [f"file_{i}.mp3" for i in range(30)] + [None] * 80
        self.service.check_and_submit_batches(available_files, "/mock/out", 1.0)
        
        # Verify no batches submitted
        self.assertEqual(len(self.service._batch_submitted), 0)
        
        # 2. Fill first batch (40 files)
        for i in range(30, 40):
             batch_idx = self.service.mark_chunk_ready(i, duration=1.0)
             if batch_idx is not None:
                 available_files = [f"file_{k}.mp3" for k in range(40)] + [None] * 70
                 self.service.submit_batch_by_index(batch_idx, available_files[:40], "/mock/out", 1.0, "test-id")
        
        # Verify 1 batch submitted
        self.assertEqual(len(self.service._batch_submitted), 1)

    def test_assembly_strategy_selection(self):
        """test_assembly_strategy_selection: Вибір між fast та full стратегіями."""
        # Case 1: All batches ready and speed is 1.0 -> Fast assembly
        self.service.reset(total_chunks=80) # 2 batches of 40
        self.service._batch_results = {0: "part0.mp3", 1: "part1.mp3"}
        
        with patch.object(self.service, '_assemble_fast') as mock_fast, \
             patch.object(self.service, '_assemble_full') as mock_full:
            self.service.assemble_final(Path("out.mp3"), [], 1.0, "test-id")
            mock_fast.assert_called_once()
            mock_full.assert_not_called()

        # Case 2: Batches not ready or missing -> Full assembly (fallback)
        self.service.reset(total_chunks=80)
        self.service._batch_results = {0: "part0.mp3"} # missing 1
        
        with patch.object(self.service, '_assemble_fast') as mock_fast, \
             patch.object(self.service, '_assemble_full') as mock_full:
            self.service.assemble_final(Path("out.mp3"), ["chunk1.mp3", "chunk2.mp3"], 1.5, "test-id")
            mock_fast.assert_not_called()
            mock_full.assert_called_once()

    def test_cleanup_after_assembly(self):
        """test_cleanup_after_assembly: Перевірка видалення тимчасових файлів part_X.mp3."""
        self.service.reset(total_chunks=80)
        self.service._batch_results = {0: "/mock/part0.mp3", 1: "/mock/part1.mp3"}
        
        with patch('src.application.services.assembly_service.Path') as mock_path_class:
            mock_p0 = MagicMock()
            mock_p1 = MagicMock()
            mock_path_class.side_effect = lambda p: mock_p0 if "part0" in p else mock_p1
            
            mock_p0.exists.return_value = True
            mock_p1.exists.return_value = True
            
            # Run final assembly
            self.service.assemble_final(Path("out.mp3"), [], 1.0, "test-id")
            
            # Verify unlink was called for both parts
            mock_p0.unlink.assert_called_once()
            mock_p1.unlink.assert_called_once()

if __name__ == '__main__':
    unittest.main()
