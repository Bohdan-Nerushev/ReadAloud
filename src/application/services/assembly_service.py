"""
Assembly Service.

This module handles the orchestration of audio assembly tasks, including
batch assembly, final concatenation, and managing assembly background threads.
"""

import logging
import threading
from typing import List, Dict, Set, Optional, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QObject, pyqtSignal

from src.domain.audio_assembler import AudioAssembler
from src.domain.models import GenerationTask, ProjectConfig

class AssemblyService(QObject):
    """
    Service responsible for assembling audio chunks into final files.
    Manages intermediate batch assembly and final merge.
    """
    
    assemblyProgressUpdated = pyqtSignal(float, float) # percentage, remaining
    batchAssemblyFinished = pyqtSignal(int, str)       # batch_index, file_path
    assemblyError = pyqtSignal(str)
    
    BATCH_SIZE = 50

    def __init__(self, audio_assembler: AudioAssembler) -> None:
        """Initialize AssemblyService."""
        super().__init__()
        self._audio_assembler = audio_assembler
        
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._batch_submitted: Set[int] = set()
        self._batch_results: Dict[int, str] = {}
        self._state_lock = threading.Lock()
        self._chunks_count = 0

    def reset(self, total_chunks: int) -> None:
        """Resets state for a new task."""
        with self._state_lock:
            self._batch_submitted.clear()
            self._batch_results.clear()
            self._chunks_count = total_chunks

    def check_and_submit_batches(
        self, 
        available_files: List[Optional[str]], 
        output_dir: str,
        speed: float
    ) -> None:
        """
        Checks if any new batches are ready to be assembled and submits them.
        """
        if self._chunks_count == 0:
            return

        total_batches = (self._chunks_count + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        
        for i in range(total_batches):
            with self._state_lock:
                if i in self._batch_submitted:
                    continue
            
            start_idx = i * self.BATCH_SIZE
            end_idx = min(start_idx + self.BATCH_SIZE, self._chunks_count)
            
            # Check range bounds against available files length
            if end_idx > len(available_files):
                continue

            # Check if all files in this range are generated (not None)
            if all(available_files[k] is not None for k in range(start_idx, end_idx)):
                self._submit_batch(i, available_files[start_idx:end_idx], output_dir, speed)

    def _submit_batch(self, batch_index: int, files: List[str], output_dir: str, speed: float) -> None:
        """Submits a batch assembly task."""
        with self._state_lock:
            self._batch_submitted.add(batch_index)

        part_path = Path(output_dir) / f"part_{batch_index}.mp3"

        def _assemble_task():
            try:
                self._audio_assembler.assemble_audio(
                    files,
                    str(part_path),
                    speed=speed,
                    copy_codec=False # Batches need re-encoding if speed != 1.0, or always for safety?
                    # Original code used copy_codec=False for batches, which is correct because 
                    # we want to apply speed at batch level if possible? 
                    # Wait, original code:
                    # self._assemble_full used speed=speed, copy_codec=False
                    # self._assemble_fast used speed=1.0, copy_codec=True
                    # batch assembly used speed=task.config.speed, copy_codec=False
                )
                return str(part_path)
            except Exception as e:
                logging.error(f"Batch {batch_index} assembly failed: {e}")
                raise

        future = self._executor.submit(_assemble_task)
        
        def _done_callback(fut):
            try:
                result_path = fut.result()
                with self._state_lock:
                    self._batch_results[batch_index] = result_path
                self.batchAssemblyFinished.emit(batch_index, result_path)
            except Exception as e:
                self.assemblyError.emit(f"Batch {batch_index} failed: {str(e)}")

        future.add_done_callback(_done_callback)

    def assemble_final(
        self, 
        output_path: Path, 
        all_chunk_files: List[Optional[str]], 
        speed: float
    ) -> None:
        """
        Performs the final assembly.
        Blocking call - should be run in a separate thread by the caller.
        """
        # Wait for any pending batches
        self._executor.shutdown(wait=True)
        # Re-create executor for next time (though reset should probably handle this, strict rule: reset creates new executor?)
        # Better: keep executor alive, just wait. shutdown(wait=True) kills it.
        # We need to restart it if we kill it. 
        # For this implementation, we will just wait. But shutdown kills it.
        # Let's just wait on futures if we kept them. 
        # But for simplicity, let's assume `check_and_submit_batches` was driving it.
        # If we are here, we assume generation is done.
        
        # Determine strategy
        parts, use_fast = self._get_assembly_parts()
        
        try:
            if use_fast and parts:
                self._assemble_fast(parts, output_path)
            else:
                # Fallback to assembling all raw files
                valid_files = [f for f in all_chunk_files if f is not None]
                if not valid_files:
                     raise Exception("No valid audio files to assemble")
                self._assemble_full(valid_files, output_path, speed)
        except Exception as e:
            raise e

        # Reinstate executor for next run
        self._executor = ThreadPoolExecutor(max_workers=2)

    def _get_assembly_parts(self) -> Tuple[List[str], bool]:
        """Check if we have all batch parts."""
        if self._chunks_count == 0:
            return [], False
            
        total_batches = (self._chunks_count + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        ordered = []
        with self._state_lock:
            for i in range(total_batches):
                if i not in self._batch_results:
                    return [], False
                ordered.append(self._batch_results[i])
        return ordered, True

    def _assemble_fast(self, parts: List[str], output_path: Path) -> None:
        logging.info("Fast assembly")
        self._audio_assembler.assemble_audio(
            parts, 
            str(output_path), 
            speed=1.0, 
            callback=self._progress_callback, 
            copy_codec=True
        )

    def _assemble_full(self, files: List[str], output_path: Path, speed: float) -> None:
        logging.info("Full assembly")
        self._audio_assembler.assemble_audio(
            files,
            str(output_path),
            speed=speed,
            callback=self._progress_callback,
            copy_codec=False
        )

    def _progress_callback(self, percentage: float, remaining: float) -> None:
        self.assemblyProgressUpdated.emit(percentage, remaining)

    def stop(self) -> None:
        """Stops all running assembly tasks."""
        self._executor.shutdown(wait=False, cancel_futures=True)
