"""
Generation Service.

This module handles the orchestration of audio generation tasks, including
thread management, retry logic, and progress tracking for generation.
"""

import logging
from typing import List, Optional, Set, Any
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal

from src.domain.models import GenerationTask, AudioChunk, ProjectConfig
from src.domain.audio_generator import AudioGenerator
from src.infrastructure.thread_manager import ThreadManager
from src.infrastructure.retry_handler import RetryHandler
from src.infrastructure.progress_tracker import ProgressTracker

class GenerationService(QObject):
    """
    Service responsible for managing the audio generation workflow.
    Delegates low-level generation to AudioGenerator and manages concurrency.
    """
    
    # Signals to communicate with Controller/UI
    progressUpdated = pyqtSignal(int, int, str)  # processed, total, eta
    chunkGenerated = pyqtSignal(int, str)        # chunk_number, file_path
    batchFailed = pyqtSignal(list, str)          # list of chunks, error message
    errorOccurred = pyqtSignal(str)

    BATCH_GEN_SIZE = 10

    def __init__(
        self,
        audio_generator: AudioGenerator,
        retry_handler: RetryHandler
    ) -> None:
        """
        Initialize the GenerationService.
        """
        super().__init__()
        self._audio_generator = audio_generator
        self._retry_handler = retry_handler
        
        self._thread_manager: Optional[ThreadManager] = None
        self._progress_tracker: Optional[ProgressTracker] = None
        self._current_task: Optional[GenerationTask] = None
        self._chunks: List[AudioChunk] = []
        self._output_dir: Optional[str] = None
        
        self._is_stopped = False

    def start_generation(
        self,
        task: GenerationTask,
        chunks: List[AudioChunk],
        output_dir: str
    ) -> None:
        """
        Starts the generation process for the given task.
        """
        self._current_task = task
        self._chunks = chunks
        self._output_dir = output_dir
        self._is_stopped = False
        
        # Initialize thread manager for this task
        if self._thread_manager:
            self._thread_manager.stop()
            
        self._thread_manager = ThreadManager(thread_count=task.config.thread_count)
        self._thread_manager.start()

        self._progress_tracker = ProgressTracker(total_chunks=len(chunks))
        self._progress_tracker.start()
        
        self._submit_chunks_to_threads()

    def _submit_chunks_to_threads(self) -> None:
        """Submits chunks in batches."""
        if not self._current_task:
            return

        for i in range(0, len(self._chunks), self.BATCH_GEN_SIZE):
            batch = self._chunks[i:i + self.BATCH_GEN_SIZE]
            self._thread_manager.submit_task(
                self._generate_batch_safe,
                batch,
                self._current_task.config.language,
                self._current_task.config.gender
            )

    def _generate_batch_safe(
        self,
        batch: List[AudioChunk],
        language: str,
        gender: str
    ) -> None:
        """
        Executed by worker thread. Generates audio for a batch.
        """
        if self._is_stopped:
            return

        try:
            audio_paths = self._retry_handler.execute_with_retry(
                self._audio_generator.generate_audio_batch,
                batch,
                language,
                gender,
                self._output_dir,
                max_retries=5,
                backoff=2.0
            )
            
            # Emit success for each chunk
            for i, chunk in enumerate(batch):
                if self._is_stopped: 
                    break
                self.chunkGenerated.emit(chunk.chunk_number, audio_paths[i])
                self._update_internal_progress(success=True)

        except Exception as e:
            logging.error(f"Generation batch failed: {e}")
            self.batchFailed.emit(batch, str(e))
            for _ in batch:
                self._update_internal_progress(success=False)

    def _update_internal_progress(self, success: bool) -> None:
        """Updates tracker and emits progress."""
        if self._progress_tracker:
            self._progress_tracker.update_progress(success)
            # We don't emit signal here constantly to avoid flooding UI thread from worker threads.
            # Ideally the Controller polls this service or we use a throttled signal.
            # But the Controller logic had a timer. 
            # We can replicate the timer logic in the Controller (polling this service) 
            # or emit safely. Since these run in threads, emitting signals is thread-safe in PyQt.
            pass

    def get_progress_info(self) -> tuple[int, int, str]:
        """Returns (processed, total, eta_string)."""
        if self._progress_tracker:
             return (
                 self._progress_tracker.get_processed_count(),
                 self._progress_tracker.get_total_count(),
                 self._progress_tracker.get_eta_string()
             )
        return 0, 0, ""

    def get_progress_percentage(self) -> float:
        if self._progress_tracker:
            return self._progress_tracker.get_progress_percentage()
        return 0.0

    def pause(self) -> bool:
        """Pauses/Resumes. Returns True if PAUSED, False if RUNNING."""
        if self._thread_manager.is_paused():
            self._thread_manager.resume()
            return False
        else:
            self._thread_manager.pause()
            return True

    def is_paused(self) -> bool:
        return self._thread_manager.is_paused()

    def stop(self) -> None:
        """Stops generation."""
        self._is_stopped = True
        self._thread_manager.stop()
