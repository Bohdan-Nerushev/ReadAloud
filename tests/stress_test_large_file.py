import sys
import os
import time
import logging
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.application.services.assembly_service import AssemblyService
from src.domain.audio_assembler import AudioAssembler
from src.domain.models import AudioChunk

class TestLargeFilePerformance(unittest.TestCase):
    def setUp(self):
        self.mock_assembler = MagicMock(spec=AudioAssembler)
        self.service = AssemblyService(self.mock_assembler)
        self.total_chunks = 5200
        self.service.reset(self.total_chunks)
        
    def test_batch_recognition_performance(self):
        """
        Verify that recognizing 5200 chunks as ready and submitting 104 batches
        doesn't cause significant overhead or O(N^2) behavior.
        """
        start_time = time.time()
        
        ready_batches = []
        for i in range(self.total_chunks):
            batch_idx = self.service.mark_chunk_ready(i, duration=1.5)
            if batch_idx is not None:
                ready_batches.append(batch_idx)
                # Simulate controller submitting batch
                self.service.submit_batch_by_index(
                    batch_idx, 
                    [f"file_{k}.mp3" for k in range(i-39, i+1)], 
                    "/tmp", 
                    1.0,
                    "stress-test"
                )
        
        # Wait for all background assembly tasks to finish
        self.service._executor.shutdown(wait=True)
        
        end_time = time.time()
        elapsed = end_time - start_time
        
        print(f"\nTime to process {self.total_chunks} chunks: {elapsed:.4f}s")
        
        # In the old O(N^2) implementation, checking 5200 chunks * 104 batches 
        # would be 540,800 operations per call, and we call it 5200 times.
        # With O(1) it should be very fast (< 0.5s even on slow systems).
        self.assertLess(elapsed, 1.0, f"Performance too slow: {elapsed}s")
        self.assertEqual(len(ready_batches), 5200 // 40)
        self.assertEqual(self.mock_assembler.assemble_audio.call_count, 130)

    def test_duration_caching_benefit(self):
        """
        Ensure that durations are passed to AudioAssembler, avoiding ffprobe.
        """
        # Mark first batch as ready
        for i in range(40):
            self.service.mark_chunk_ready(i, duration=2.0)
            
        self.service.submit_batch_by_index(0, ["f.mp3"]*40, "/tmp", 1.0, "stress-test")
        
        # Check if assemble_audio was called with durations
        args, kwargs = self.mock_assembler.assemble_audio.call_args
        self.assertIn('durations', kwargs)
        self.assertEqual(len(kwargs['durations']), 40)
        self.assertEqual(kwargs['durations'][0], 2.0)

if __name__ == '__main__':
    unittest.main()
