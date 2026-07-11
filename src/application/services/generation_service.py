import logging
import asyncio
from typing import List, Optional, Set, Any
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal

from src.domain.models import GenerationTask, AudioChunk, ProjectConfig
from src.domain.audio_generator import AudioGenerator
from src.infrastructure.thread_manager import ThreadManager
from src.infrastructure.retry_handler import RetryHandler
from src.infrastructure.progress_tracker import ProgressTracker
from src.infrastructure.logging_config import set_correlation_id

class GenerationService(QObject):
    """
    Service responsible for managing the audio generation workflow.
    Delegates low-level generation to AudioGenerator and manages concurrency.
    """
    
    # Signals to communicate with Controller/UI
    progressUpdated = pyqtSignal(int, int, str, float)  # processed, total, eta, speed
    chunkGenerated = pyqtSignal(int, str, float) # chunk_number, file_path, duration
    batchGenerated = pyqtSignal(list)             # list of (chunk_number, file_path, duration)
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
            
        self._thread_manager = ThreadManager(thread_count=1)
        self._thread_manager.start()

        self._progress_tracker = ProgressTracker(total_chunks=len(chunks))
        completed_count = sum(1 for c in chunks if c.audio_file_path is not None)
        self._progress_tracker.start(completed_chunks=completed_count)
        
        self._submit_chunks_to_threads()

    def _submit_chunks_to_threads(self) -> None:
        """Submits chunks in batches sequentially."""
        if not self._current_task:
            return

        correlation_id = str(self._current_task.id)

        pending_chunks = [c for c in self._chunks if c.audio_file_path is None]
        max_workers = min(self._current_task.config.thread_count, 10)

        for i in range(0, len(pending_chunks), self.BATCH_GEN_SIZE):
            batch = pending_chunks[i:i + self.BATCH_GEN_SIZE]
            self._thread_manager.submit_task(
                self._generate_batch_safe,
                batch,
                self._current_task.config.language,
                self._current_task.config.gender,
                correlation_id,
                max_workers
            )

    def _on_chunk_generated_callback(self, chunk_number: int, file_path: str, duration: float) -> None:
        """Callback from AudioGenerator when a single chunk is generated."""
        if self._is_stopped:
            return
        logging.debug(f"Chunk {chunk_number} generated: {file_path} ({duration}s)")
        self.chunkGenerated.emit(chunk_number, file_path, duration)
        self._update_internal_progress(success=True)

    def _generate_batch_safe(
        self,
        batch: List[AudioChunk],
        language: str,
        gender: str,
        correlation_id: str,
        max_workers: int
    ) -> None:
        """
        Executed by worker thread. Generates audio for a batch.
        """
        if self._is_stopped:
            return

        # Set correlation ID for this worker thread
        set_correlation_id(correlation_id)

        try:
            results = self._audio_generator.generate_audio_batch(
                batch,
                language,
                gender,
                self._output_dir,
                chunk_callback=self._on_chunk_generated_callback,
                max_workers=max_workers
            )
            
            batch_results = []
            # Emit success for each chunk
            for i, chunk in enumerate(batch):
                if self._is_stopped: 
                    break
                file_path, duration = results[i]
                batch_results.append((chunk.chunk_number, file_path, duration))
            
            if not self._is_stopped:
                self.batchGenerated.emit(batch_results)

        except Exception as e:
            logging.error(f"Generation batch failed: {e}")
            self.batchFailed.emit(batch, str(e))
            for _ in batch:
                self._update_internal_progress(success=False)

    def _update_internal_progress(self, success: bool) -> None:
        """Updates tracker and emits progress."""
        if self._progress_tracker:
            self._progress_tracker.update_progress(success)
            pass

    def get_progress_info(self) -> tuple[int, int, str, float]:
        """Returns (processed, total, eta_string, chunks_per_second)."""
        if self._progress_tracker:
             return (
                 self._progress_tracker.get_processed_count(),
                 self._progress_tracker.get_total_count(),
                 self._progress_tracker.get_eta_string(),
                 self._progress_tracker.get_chunks_per_second()
             )
        return 0, 0, "", 0.0

    def get_progress_percentage(self) -> float:
        if self._progress_tracker:
            return self._progress_tracker.get_progress_percentage()
        return 0.0

    def pause(self) -> bool:
        """Pauses/Resumes. Returns True if PAUSED, False if RUNNING."""
        if self._thread_manager.is_paused():
            self._thread_manager.resume()
            if self._progress_tracker:
                self._progress_tracker.resume()
            return False
        else:
            self._thread_manager.pause()
            if self._progress_tracker:
                self._progress_tracker.pause()
            return True

    def is_paused(self) -> bool:
        return self._thread_manager.is_paused()

    def stop(self) -> None:
        """Stops generation."""
        self._is_stopped = True
        if self._thread_manager:
            self._thread_manager.stop()


