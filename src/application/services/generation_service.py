"""
Generation Service.

Orchestrates audio generation workflow: delegates low-level synthesis to
AudioGenerator and manages concurrency via ThreadManager.

Changes vs. original:
    - _generate_batch_safe now handles partial batch success (BUG-2 consequence):
      individual None results from AudioGenerator are filtered out; only the
      successful chunks are reported. This prevents a single failed chunk from
      stopping the entire pipeline.
    - Added _is_stopped guard before each batch submission.
    - Improved logging for observability.
"""

import logging
from typing import List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from src.domain.models import GenerationTask, AudioChunk
from src.domain.audio_generator import AudioGenerator
from src.infrastructure.thread_manager import ThreadManager
from src.infrastructure.progress_tracker import ProgressTracker
from src.infrastructure.logging_config import set_correlation_id


class GenerationService(QObject):
    """
    Service responsible for managing the audio generation workflow.

    Delegates low-level generation to AudioGenerator and manages concurrency
    via ThreadManager.  Handles partial batch success gracefully so that a
    single failing chunk never aborts the pipeline.
    """

    # Signals to communicate with the Controller / UI
    progressUpdated = pyqtSignal(int, int, str, float)   # processed, total, eta, speed
    chunkGenerated = pyqtSignal(int, str, float)          # chunk_number, file_path, duration
    chunkFailed = pyqtSignal(int, str)                    # chunk_number, error_message
    batchGenerated = pyqtSignal(list)                     # list of (chunk_number, file_path, duration)
    batchFailed = pyqtSignal(list, str)                   # list of chunks, error message
    errorOccurred = pyqtSignal(str)

    # Chunks per batch submitted to the thread pool
    BATCH_GEN_SIZE = 10

    def __init__(self, audio_generator: AudioGenerator) -> None:
        """Initialize the GenerationService."""
        super().__init__()
        self._audio_generator = audio_generator

        self._thread_manager: Optional[ThreadManager] = None
        self._progress_tracker: Optional[ProgressTracker] = None
        self._current_task: Optional[GenerationTask] = None
        self._chunks: List[AudioChunk] = []
        self._output_dir: Optional[str] = None

        self._is_stopped = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start_generation(
            self,
            task: GenerationTask,
            chunks: List[AudioChunk],
            output_dir: str
    ) -> None:
        """Starts the generation process for the given task."""
        self._current_task = task
        self._chunks = chunks
        self._output_dir = output_dir
        self._is_stopped = False

        # Shutdown any previous thread manager before creating a new one
        if self._thread_manager:
            self._thread_manager.stop()

        self._thread_manager = ThreadManager(thread_count=task.config.thread_count)
        self._thread_manager.start()

        completed_count = sum(1 for c in chunks if c.audio_file_path is not None)
        self._progress_tracker = ProgressTracker(total_chunks=len(chunks))
        self._progress_tracker.start(completed_chunks=completed_count)

        self._submit_chunks_to_threads()

    def pause(self) -> bool:
        """Pauses or resumes generation. Returns True if now PAUSED, False if RUNNING."""
        if self._thread_manager is None:
            return False

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
        """Returns True if generation is currently paused."""
        return self._thread_manager is not None and self._thread_manager.is_paused()

    def stop(self) -> None:
        """Stops all generation work immediately and closes active threads."""
        self._is_stopped = True
        if self._thread_manager:
            self._thread_manager.stop()

    # ------------------------------------------------------------------
    # Progress queries
    # ------------------------------------------------------------------

    def get_progress_info(self) -> Tuple[int, int, str, float]:
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
        """Returns completion percentage (0–100)."""
        if self._progress_tracker:
            return self._progress_tracker.get_progress_percentage()
        return 0.0

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _submit_chunks_to_threads(self) -> None:
        """Submits pending chunks to the thread pool in fixed-size batches."""
        if not self._current_task:
            return

        correlation_id = str(self._current_task.id)
        pending_chunks = [c for c in self._chunks if c.audio_file_path is None]
        max_workers = min(self._current_task.config.thread_count, 10)

        logging.info(
            f"Submitting {len(pending_chunks)} pending chunk(s) "
            f"in batches of {self.BATCH_GEN_SIZE} "
            f"(task {self._current_task.id})"
        )

        for i in range(0, len(pending_chunks), self.BATCH_GEN_SIZE):
            if self._is_stopped:
                break
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
        """Callback from AudioGenerator when a single chunk is successfully generated."""
        if self._is_stopped:
            return
        logging.debug(f"Chunk {chunk_number} generated: {file_path} ({duration:.2f}s)")
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
        Executed by a worker thread. Generates audio for a batch of chunks.
        """
        if self._is_stopped:
            return

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
            failed_chunks: List[AudioChunk] = []

            for chunk, result in zip(batch, results):
                if self._is_stopped:
                    break

                if result is None:
                    # This chunk failed after all retries — log and count failure, but do NOT abort pipeline
                    failed_chunks.append(chunk)
                    self._update_internal_progress(success=False)
                    logging.warning(
                        f"Chunk {chunk.chunk_number} permanently failed — "
                        "skipping and continuing pipeline."
                    )
                    self.chunkFailed.emit(chunk.chunk_number, "Failed after max retries")
                else:
                    file_path, duration = result
                    batch_results.append((chunk.chunk_number, file_path, duration))

            if batch_results and not self._is_stopped:
                self.batchGenerated.emit(batch_results)

            # Only emit batchFailed if ALL chunks in the batch failed permanently
            if failed_chunks and len(failed_chunks) == len(batch) and not self._is_stopped:
                logging.error(f"All {len(batch)} chunk(s) in batch failed permanently.")
                self.batchFailed.emit(
                    failed_chunks,
                    f"All {len(failed_chunks)} chunk(s) in batch failed permanently after max retries"
                )

        except Exception as e:
            # Unexpected error in the batch runner itself (not per-chunk)
            logging.error(f"Unexpected error in batch generation: {e}", exc_info=True)
            if not self._is_stopped:
                self.batchFailed.emit(batch, str(e))
                for _ in batch:
                    self._update_internal_progress(success=False)

    def _update_internal_progress(self, success: bool) -> None:
        """Updates the progress tracker."""
        if self._progress_tracker:
            self._progress_tracker.update_progress(success)
