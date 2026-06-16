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
    
    BATCH_SIZE = 40

    def __init__(self, audio_assembler: AudioAssembler) -> None:
        """Initialize AssemblyService."""
        super().__init__()
        self._audio_assembler = audio_assembler
        
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._batch_submitted: Set[int] = set()
        self._batch_results: Dict[int, str] = {}
        self._batch_durations: Dict[int, List[float]] = {} # batch_index -> list of chunk durations
        self._batch_ready_counts: Dict[int, int] = {} # batch_index -> count of ready chunks
        
        self._state_lock = threading.Lock()
        self._chunks_count = 0

    def reset(self, total_chunks: int) -> None:
        """Resets state for a new task."""
        with self._state_lock:
            self._batch_submitted.clear()
            self._batch_results.clear()
            self._batch_durations.clear()
            self._batch_ready_counts.clear()
            self._chunks_count = total_chunks
            
            # Initialize ready counts for all batches
            total_batches = (total_chunks + self.BATCH_SIZE - 1) // self.BATCH_SIZE
            for i in range(total_batches):
                self._batch_ready_counts[i] = 0
                self._batch_durations[i] = [0.0] * self._get_batch_size(i)

    def _get_batch_size(self, batch_index: int) -> int:
        """Returns the number of chunks in a given batch."""
        start_idx = batch_index * self.BATCH_SIZE
        if start_idx >= self._chunks_count:
            return 0
        return min(self.BATCH_SIZE, self._chunks_count - start_idx)

    def mark_chunk_ready(self, chunk_index: int, duration: float) -> Optional[int]:
        """
        Marks a chunk as ready and returns batch_index if the batch is now complete.
        
        Args:
            chunk_index: 0-based index of the chunk
            duration: duration of the chunk in seconds
            
        Returns:
            Internal batch_index if ready to be submitted, None otherwise
        """
        batch_index = chunk_index // self.BATCH_SIZE
        with self._state_lock:
            if batch_index not in self._batch_ready_counts:
                return None
                
            self._batch_ready_counts[batch_index] += 1
            
            # Store duration in the relative position within batch
            rel_idx = chunk_index % self.BATCH_SIZE
            self._batch_durations[batch_index][rel_idx] = duration
            
            if self._batch_ready_counts[batch_index] == self._get_batch_size(batch_index):
                if batch_index not in self._batch_submitted:
                    return batch_index
        return None

    def check_and_submit_batches(
        self, 
        available_files: List[Optional[str]], 
        output_dir: str,
        speed: float,
        correlation_id: str = "legacy-batch"
    ) -> None:
        """
        No longer used for primary polling, but kept for compatibility or 
        manual triggers if needed. Uses the optimized logic.
        """
        if self._chunks_count == 0:
            return

        total_batches = (self._chunks_count + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        
        for i in range(total_batches):
            is_ready = False
            with self._state_lock:
                if i in self._batch_submitted:
                    continue
                if self._batch_ready_counts.get(i, 0) == self._get_batch_size(i):
                    is_ready = True
            
            if is_ready:
                start_idx = i * self.BATCH_SIZE
                end_idx = min(start_idx + self.BATCH_SIZE, self._chunks_count)
                files = available_files[start_idx:end_idx]
                
                # Double check all files are present if we use this method
                if all(f is not None for f in files):
                    self.submit_batch_by_index(i, files, output_dir, speed, correlation_id)

    def submit_batch_by_index(self, batch_index: int, files: List[str], output_dir: str, speed: float, correlation_id: str) -> None:
        """Submits a batch assembly task by index using optimized duration info."""
        with self._state_lock:
            if batch_index in self._batch_submitted:
                return
            self._batch_submitted.add(batch_index)
            durations = list(self._batch_durations[batch_index])

        part_path = Path(output_dir) / f"part_{batch_index}.mp3"
        logging.info(f"Submitting batch {batch_index} for assembly ({len(files)} files)")

        def _assemble_task():
            from src.infrastructure.logging_config import set_correlation_id
            set_correlation_id(correlation_id)
            try:
                self._audio_assembler.assemble_audio(
                    files,
                    str(part_path),
                    speed=speed,
                    copy_codec=False,
                    durations=durations
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
        speed: float,
        correlation_id: str
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
                self._assemble_fast(parts, output_path, correlation_id)
            else:
                # Fallback to assembling all raw files
                valid_files = [f for f in all_chunk_files if f is not None]
                if not valid_files:
                    raise Exception("No valid audio files to assemble")
                self._assemble_full(valid_files, output_path, speed, correlation_id)
        finally:
            # Cleanup temporary part files
            if parts:
                for part in parts:
                    try:
                        p = Path(part)
                        if p.exists():
                            p.unlink()
                            logging.debug(f"Deleted temporary part: {part}")
                    except Exception as e:
                        logging.warning(f"Failed to delete temporary part {part}: {e}")

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

    def _assemble_fast(self, parts: List[str], output_path: Path, correlation_id: str) -> None:
        from src.infrastructure.logging_config import set_correlation_id
        set_correlation_id(correlation_id)
        logging.info("Fast assembly")
        self._audio_assembler.assemble_audio(
            parts, 
            str(output_path), 
            speed=1.0, 
            callback=self._progress_callback, 
            copy_codec=True
        )

    def _assemble_full(self, files: List[str], output_path: Path, speed: float, correlation_id: str) -> None:
        from src.infrastructure.logging_config import set_correlation_id
        set_correlation_id(correlation_id)
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
