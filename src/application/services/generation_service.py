"""
Generation Service.

Orchestrates the audio generation workflow: delegates synthesis to a TTS generator and
manages concurrency via ThreadManager. A chunk that fails permanently is reported and
skipped; it never aborts the rest of the pipeline.
"""

import functools
import logging
import threading
from typing import List, Optional, Set, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from src.constants import DEFAULT_MAX_CHUNK_RETRIES, GENERATION_BATCH_SIZE, MAX_PARALLEL_BATCHES
from src.domain.exceptions import is_fatal_setup_error
from src.domain.models import GenerationTask, AudioChunk
from src.domain.retry_budget import RetryBudget
from src.domain.tts_generator_protocol import TtsGeneratorProtocol
from src.infrastructure.thread_manager import ThreadManager
from src.infrastructure.progress_tracker import ProgressTracker
from src.infrastructure.logging_config import set_correlation_id


def plan_concurrency(thread_count: int, batch_count: int) -> Tuple[int, int]:
    """
    Splits the requested request concurrency between parallel batches and per-batch workers.

    ``thread_count`` is the total number of synthesis requests allowed in flight. With
    enough batches each batch gets one worker; with few batches the spare concurrency
    goes into the batch, so the product never exceeds ``thread_count`` (it used to be
    ``thread_count`` batches x ``thread_count`` workers).

    Returns:
        (parallel_batches, workers_per_batch)
    """
    total = max(1, min(thread_count, MAX_PARALLEL_BATCHES))
    parallel_batches = max(1, min(total, batch_count))
    return parallel_batches, max(1, total // parallel_batches)


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
    generationAborted = pyqtSignal(str)                   # unrecoverable batch-level error (real reason)

    # Chunks per batch submitted to the thread pool
    BATCH_GEN_SIZE = GENERATION_BATCH_SIZE

    @classmethod
    def _batch_count(cls, chunk_count: int) -> int:
        return -(-chunk_count // cls.BATCH_GEN_SIZE)

    def __init__(self, audio_generator: TtsGeneratorProtocol) -> None:
        """Initialize the GenerationService."""
        super().__init__()
        self._audio_generator = audio_generator

        self._thread_manager: Optional[ThreadManager] = None
        self._progress_tracker: Optional[ProgressTracker] = None
        self._current_task: Optional[GenerationTask] = None
        self._chunks: List[AudioChunk] = []
        self._output_dir: Optional[str] = None

        self._is_stopped = False
        # Incremented on every start/stop; callbacks of older generations are dropped.
        self._generation_id = 0
        self._id_lock = threading.Lock()

    @property
    def generation_id(self) -> int:
        """Identifier of the current generation session (changes on start and stop)."""
        return self._generation_id

    @property
    def last_error(self) -> Optional[str]:
        """Most recent per-chunk error reported by the active generator, if any."""
        return self._audio_generator.last_error

    @property
    def has_worker_pool(self) -> bool:
        """True once a generation has started and its worker pool exists."""
        return self._thread_manager is not None

    @staticmethod
    def _pool_size(task: GenerationTask) -> int:
        return max(1, min(task.config.effective_thread_count, MAX_PARALLEL_BATCHES))

    def _next_generation_id(self) -> int:
        with self._id_lock:
            self._generation_id += 1
            return self._generation_id

    def switch_generator(self, generator: TtsGeneratorProtocol) -> None:
        """
        Replaces the active TTS generator with a different implementation.

        Must be called before start_generation(), not during active synthesis.
        Allows AppController to switch between Edge TTS and Piper per task.

        Args:
            generator: New TTS generator conforming to TtsGeneratorProtocol.
        """
        self._audio_generator = generator
        logging.info("TTS generator switched to: %s", type(generator).__name__)

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
        # Cancel leftovers of the previous generation before invalidating its id
        if self._thread_manager:
            self._cancel_generator()
            self._thread_manager.stop()
        self._next_generation_id()
        self._set_generator_paused(False)
        self._audio_generator.last_error = None

        self._current_task = task
        self._chunks = chunks
        self._output_dir = output_dir
        self._is_stopped = False

        self._thread_manager = ThreadManager(thread_count=self._pool_size(task))
        self._thread_manager.start()
        pending = sum(1 for c in chunks if c.audio_file_path is None)
        self._audio_generator.set_retry_budget(RetryBudget.for_chunks(pending))

        completed_count = sum(1 for c in chunks if c.audio_file_path is not None)
        self._progress_tracker = ProgressTracker(total_chunks=len(chunks))
        self._progress_tracker.start(completed_chunks=completed_count)

        self._submit_chunks_to_threads()

    def retry_chunks(
            self,
            task: GenerationTask,
            chunks_to_retry: List[AudioChunk],
            output_dir: str
    ) -> None:
        """Resubmits specific missing or failed chunks for a retry sweep."""
        if not chunks_to_retry or self._is_stopped:
            return

        logging.info(f"Executing retry sweep for {len(chunks_to_retry)} chunk(s)...")
        if self._progress_tracker:
            self._progress_tracker.begin_retry(c.chunk_number for c in chunks_to_retry)

        correlation_id = str(task.id)
        _, max_workers = plan_concurrency(
            task.config.effective_thread_count, self._batch_count(len(chunks_to_retry))
        )

        if not self._thread_manager:
            self._thread_manager = ThreadManager(thread_count=self._pool_size(task))
            self._thread_manager.start()

        for i in range(0, len(chunks_to_retry), self.BATCH_GEN_SIZE):
            if self._is_stopped:
                break
            batch = chunks_to_retry[i:i + self.BATCH_GEN_SIZE]
            self._thread_manager.submit_task(
                functools.partial(
                    self._generate_batch_safe,
                    generation_id=self._generation_id,
                    output_dir=output_dir,
                ),
                batch,
                task.config.language,
                task.config.gender,
                correlation_id,
                max_workers,
                DEFAULT_MAX_CHUNK_RETRIES,
            )

    def pause(self) -> bool:
        """Pauses or resumes generation. Returns True if now PAUSED, False if RUNNING."""
        if self._thread_manager is None:
            return False

        if self._thread_manager.is_paused():
            self._thread_manager.resume()
            self._set_generator_paused(False)
            if self._progress_tracker:
                self._progress_tracker.resume()
            return False
        else:
            self._thread_manager.pause()
            self._set_generator_paused(True)
            if self._progress_tracker:
                self._progress_tracker.pause()
            return True

    def _set_generator_paused(self, paused: bool) -> None:
        """Propagates pause/resume into a batch that is already running inside the generator."""
        try:
            (self._audio_generator.pause if paused else self._audio_generator.resume)()
        except Exception as exc:
            logging.warning("Generator pause/resume failed: %s", exc)

    def is_paused(self) -> bool:
        """Returns True if generation is currently paused."""
        return self._thread_manager is not None and self._thread_manager.is_paused()

    def stop(self, wait: bool = False) -> None:
        """Stops all generation work immediately and closes active threads."""
        self._is_stopped = True
        self._next_generation_id()
        self._cancel_generator()
        if self._thread_manager:
            self._thread_manager.stop(wait=wait)

    def _cancel_generator(self) -> None:
        """Asks the active generator to abort in-flight batches (cooperative cancellation)."""
        try:
            self._audio_generator.cancel()
        except Exception as exc:
            logging.warning("Generator cancel() failed: %s", exc)

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
        _, max_workers = plan_concurrency(
            self._current_task.config.effective_thread_count, self._batch_count(len(pending_chunks))
        )

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
                functools.partial(
                    self._generate_batch_safe,
                    generation_id=self._generation_id,
                    output_dir=self._output_dir,
                ),
                batch,
                self._current_task.config.language,
                self._current_task.config.gender,
                correlation_id,
                max_workers
            )

    def _is_current(self, generation_id: Optional[int]) -> bool:
        return not self._is_stopped and (generation_id is None or generation_id == self._generation_id)

    def _on_chunk_generated_callback(
            self,
            chunk_number: int,
            file_path: str,
            duration: float,
            generation_id: Optional[int] = None,
    ) -> None:
        """Callback from AudioGenerator when a single chunk is successfully generated."""
        if not self._is_current(generation_id):
            return
        logging.debug(f"Chunk {chunk_number} generated: {file_path} ({duration:.2f}s)")
        self.chunkGenerated.emit(chunk_number, file_path, duration)
        self._update_internal_progress(success=True, chunk_number=chunk_number)

    def _generate_batch_safe(
            self,
            batch: List[AudioChunk],
            language: str,
            gender: str,
            correlation_id: str,
            max_workers: int,
            max_retries: int = DEFAULT_MAX_CHUNK_RETRIES,
            generation_id: Optional[int] = None,
            output_dir: Optional[str] = None,
    ) -> None:
        """
        Executed by a worker thread. Generates audio for a batch of chunks.

        ``generation_id`` and ``output_dir`` are captured at submission time so a batch
        that outlives its generation can neither write into a newer task's directory
        nor report results to it.
        """
        if generation_id is None:
            generation_id = self._generation_id
        if output_dir is None:
            output_dir = self._output_dir
        if not self._is_current(generation_id):
            return

        set_correlation_id(correlation_id)

        logging.info(
            "Generating batch of %d chunk(s) via '%s' (lang='%s', gender='%s', threads=%d)",
            len(batch), type(self._audio_generator).__name__, language, gender, max_workers,
        )

        succeeded: Set[int] = set()

        def _on_chunk(chunk_number: int, file_path: str, duration: float) -> None:
            succeeded.add(chunk_number)
            self._on_chunk_generated_callback(
                chunk_number, file_path, duration, generation_id=generation_id
            )

        try:
            results = self._audio_generator.generate_audio_batch(
                batch,
                language,
                gender,
                output_dir,
                chunk_callback=_on_chunk,
                max_workers=max_workers,
                max_retries=max_retries
            )

            batch_results = []
            failed_chunks: List[AudioChunk] = []

            for chunk, result in zip(batch, results):
                if not self._is_current(generation_id):
                    break

                if result is None:
                    # This chunk failed after all retries — log and count failure, but do NOT abort pipeline
                    failed_chunks.append(chunk)
                    self._update_internal_progress(success=False, chunk_number=chunk.chunk_number)
                    logging.warning(
                        f"Chunk {chunk.chunk_number} permanently failed — "
                        "skipping and continuing pipeline."
                    )
                    self.chunkFailed.emit(
                        chunk.chunk_number, self.last_error or "Failed after max retries"
                    )
                else:
                    file_path, duration = result
                    batch_results.append((chunk.chunk_number, file_path, duration))

            if batch_results and self._is_current(generation_id):
                self.batchGenerated.emit(batch_results)

            # Only emit batchFailed if ALL chunks in the batch failed permanently
            if failed_chunks and len(failed_chunks) == len(batch) and self._is_current(generation_id):
                logging.error(f"All {len(batch)} chunk(s) in batch failed permanently.")
                self.batchFailed.emit(
                    failed_chunks,
                    f"All {len(failed_chunks)} chunk(s) in batch failed permanently after max retries"
                )

        except Exception as e:
            # Unexpected error in the batch runner itself (not per-chunk)
            logging.error(f"Unexpected error in batch generation ({type(self._audio_generator).__name__}): {e}", exc_info=True)
            if not self._is_current(generation_id):
                return
            if is_fatal_setup_error(e):
                self.generationAborted.emit(getattr(e, "message", None) or str(e))
                return
            if not self._is_stopped:
                # Chunks that were delivered before the batch blew up are already counted
                # as successes; only the rest of the batch failed.
                unfinished = [c for c in batch if c.chunk_number not in succeeded]
                if unfinished:
                    self.batchFailed.emit(unfinished, str(e))
                for chunk in unfinished:
                    self._update_internal_progress(success=False, chunk_number=chunk.chunk_number)

    def _update_internal_progress(self, success: bool, chunk_number: Optional[int] = None) -> None:
        """Updates the progress tracker."""
        if self._progress_tracker:
            self._progress_tracker.update_progress(success, chunk_number)
