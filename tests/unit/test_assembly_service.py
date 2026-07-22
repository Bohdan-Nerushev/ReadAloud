import unittest
import sys
import tempfile
import shutil
from unittest.mock import MagicMock, patch
from pathlib import Path

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.application.services.assembly_service import AssemblyService
from src.domain.models import AudioChunk

class TestAssemblyService(unittest.TestCase):
    def setUp(self):
        self.mock_assembler = MagicMock()
        self.service = AssemblyService(self.mock_assembler)

    def test_batch_completion_tracking(self):
        """test_batch_completion_tracking: Verification of tracking logic for waiting for all parts of a batch."""
        # Setup: 110 chunks, BATCH_SIZE=40 -> 3 batches (40, 40, 30)
        self.service.reset(total_chunks=110)
        
        # 1. Fill some files but not enough for a full batch (30 files, batch size is 40)
        for i in range(39):
            batch_idx = self.service.mark_chunk_ready(i, duration=1.0)
            self.assertIsNone(batch_idx)
            
        # 2. Fill first batch (40 files)
        batch_idx = self.service.mark_chunk_ready(39, duration=1.0)
        self.assertEqual(batch_idx, 0)

    def test_assembly_strategy_selection(self):
        """test_assembly_strategy_selection: Verification of strategy selection between fast and full assembly."""
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
        """test_cleanup_after_assembly: Verification of temporary file cleanup (part_X.mp3) after assembly."""
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

    def test_stop_and_reset_behavior(self):
        """test_stop_and_reset_behavior: Verifies that after stop() is called, reset() re-initializes the executor and submit works."""
        self.service.stop()
        self.assertIsNone(self.service._executor)
        
        self.service.reset(total_chunks=80)
        self.assertIsNotNone(self.service._executor)
        
        with patch.object(self.service._executor, 'submit') as mock_submit:
            self.service.submit_batch_by_index(0, ["file1.mp3"], "/tmp", 1.0, "correlation-id")
            mock_submit.assert_called_once()

if __name__ == '__main__':
    unittest.main()
