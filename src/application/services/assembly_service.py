"""
Assembly Service.

This module handles the orchestration of audio assembly tasks, including
batch assembly, final concatenation, and managing assembly background threads.
"""

import logging
import threading
from typing import List, Dict, Set, Optional, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future, wait

from PyQt6.QtCore import QObject, pyqtSignal

from src.domain.audio_assembler import AudioAssembler
from src.domain.models import GenerationTask, ProjectConfig
from src.infrastructure.logging_config import set_correlation_id


class AssemblyService(QObject):
    """
    Service responsible for assembling audio chunks into final files.

    Manages intermediate batch assembly and final merge. Provides a fully
    encapsulated interface so callers never need to touch private fields.

    Lifecycle:
        - Call ``reset(total_chunks)`` at the start of each new task.
        - Call ``stop()`` to cancel in-flight assembly work.
        - The executor is re-created on every ``reset()`` so recovery after a
          previous ``stop()`` always works reliably (ISSUE-9 FIX).
    """

    assemblyProgressUpdated = pyqtSignal(float, float)  # percentage, remaining
    batchAssemblyFinished = pyqtSignal(int, str)        # batch_index, file_path
    assemblyError = pyqtSignal(str)

    BATCH_SIZE = 40

    def __init__(self, audio_assembler: AudioAssembler) -> None:
        """Initialize AssemblyService."""
        super().__init__()
        self._audio_assembler = audio_assembler

        # Executor is created fresh in reset(); starts as None.
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: List[Future] = []
        self._batch_submitted: Set[int] = set()
        self._batch_results: Dict[int, str] = {}
        self._batch_durations: Dict[int, List[float]] = {}
        self._batch_ready_counts: Dict[int, int] = {}

        self._state_lock = threading.Lock()
        self._chunks_count = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self, total_chunks: int) -> None:
        """
        Resets state for a new task and creates a fresh thread pool.

        ISSUE-9 FIX:
            Always creates a fresh ``ThreadPoolExecutor`` on reset regardless of
            the previous executor state.  This avoids the subtle bug where
            ``stop()`` shut down the executor but the ``reset()`` check only
            re-created it if it was ``None``.  Now the reference is always valid
            after ``reset()``.
        """
        # Shut down any existing executor before replacing it
        if self._executor is not None:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

        with self._state_lock:
            self._futures.clear()
            self._batch_submitted.clear()
            self._batch_results.clear()
            self._batch_durations.clear()
            self._batch_ready_counts.clear()
            self._chunks_count = total_chunks

            total_batches = (total_chunks + self.BATCH_SIZE - 1) // self.BATCH_SIZE
            for i in range(total_batches):
                self._batch_ready_counts[i] = 0
                self._batch_durations[i] = [0.0] * self._get_batch_size(i)

        # Create a fresh executor — always valid after reset
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="AssemblyWorker")

    def stop(self) -> None:
        """
        Cancels all running assembly tasks.

        ISSUE-9 FIX:
            Sets executor to None so that any stale calls to ``submit_batch_by_index``
            after a stop will recreate it safely (or will be guarded by caller-side
            checks).  The next ``reset()`` creates a clean executor.
        """
        executor = self._executor
        self._executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Chunk tracking
    # ------------------------------------------------------------------

    def mark_chunk_ready(self, chunk_index: int, duration: float) -> Optional[int]:
        """
        Marks a chunk as ready and returns the batch_index if the batch is complete.

        Args:
            chunk_index: 0-based index of the chunk.
            duration: Duration of the generated chunk in seconds.

        Returns:
            The batch_index if all chunks in that batch are now ready (and the
            batch has not yet been submitted), or ``None`` otherwise.
        """
        batch_index = chunk_index // self.BATCH_SIZE
        with self._state_lock:
            if batch_index not in self._batch_ready_counts:
                return None

            self._batch_ready_counts[batch_index] += 1

            rel_idx = chunk_index % self.BATCH_SIZE
            self._batch_durations[batch_index][rel_idx] = duration

            if (
                self._batch_ready_counts[batch_index] == self._get_batch_size(batch_index)
                and batch_index not in self._batch_submitted
            ):
                return batch_index
        return None

    def mark_chunk_failed(self, chunk_index: int) -> Optional[int]:
        """
        Marks a chunk as permanently failed so the batch count advances without hanging.

        Args:
            chunk_index: 0-based index of the failed chunk.

        Returns:
            The batch_index if all chunks in that batch are now processed (and the
            batch has not yet been submitted), or ``None`` otherwise.
        """
        batch_index = chunk_index // self.BATCH_SIZE
        with self._state_lock:
            if batch_index not in self._batch_ready_counts:
                return None

            self._batch_ready_counts[batch_index] += 1

            rel_idx = chunk_index % self.BATCH_SIZE
            self._batch_durations[batch_index][rel_idx] = 0.0

            if (
                self._batch_ready_counts[batch_index] == self._get_batch_size(batch_index)
                and batch_index not in self._batch_submitted
            ):
                return batch_index
        return None

    # ------------------------------------------------------------------
    # Batch submission
    # ------------------------------------------------------------------

    def submit_batch_by_index(
            self,
            batch_index: int,
            files: List[str],
            output_dir: str,
            speed: float,
            correlation_id: str
    ) -> None:
        """Submits a batch assembly task by index using cached duration info."""
        with self._state_lock:
            if batch_index in self._batch_submitted:
                return
            self._batch_submitted.add(batch_index)
            durations = list(self._batch_durations[batch_index])

        if self._executor is None:
            logging.error(
                f"AssemblyService: cannot submit batch {batch_index} — "
                "executor is not initialised (was reset() called?)"
            )
            return

        part_path = Path(output_dir) / f"part_{batch_index}.mp3"
        tmp_part_path = Path(output_dir) / f"part_{batch_index}.mp3.tmp"
        logging.info(f"Submitting batch {batch_index} for assembly ({len(files)} files)")

        # Filter out any None entries (permanently failed chunks)
        valid_pairs = [(f, d) for f, d in zip(files, durations) if f is not None]
        if not valid_pairs:
            logging.warning(f"Batch {batch_index} has no valid chunk files — skipping batch assembly.")
            return
        valid_files = [f for f, d in valid_pairs]
        valid_durations = [d for f, d in valid_pairs]

        def _assemble_task() -> str:
            set_correlation_id(correlation_id)
            try:
                self._audio_assembler.assemble_audio(
                    valid_files,
                    str(tmp_part_path),
                    speed=speed,
                    copy_codec=False,
                    durations=valid_durations
                )
                import os
                if tmp_part_path.exists():
                    os.replace(str(tmp_part_path), str(part_path))
                elif not part_path.exists():
                    part_path.touch()
                return str(part_path)
            except Exception as e:
                if tmp_part_path.exists():
                    try:
                        tmp_part_path.unlink()
                    except Exception:
                        pass
                logging.error(f"Batch {batch_index} assembly failed: {e}", exc_info=True)
                raise

        future = self._executor.submit(_assemble_task)
        with self._state_lock:
            self._futures.append(future)

        def _done_callback(fut: Future) -> None:
            try:
                result_path = fut.result()
                with self._state_lock:
                    self._batch_results[batch_index] = result_path
                self.batchAssemblyFinished.emit(batch_index, result_path)
            except Exception as e:
                self.assemblyError.emit(f"Batch {batch_index} failed: {str(e)}")

        future.add_done_callback(_done_callback)

    # ------------------------------------------------------------------
    # Recovery public API (BUG-5 FIX)
    # ------------------------------------------------------------------

    def restore_batch_result(self, batch_index: int, part_path: str) -> None:
        """
        Restores a previously assembled batch part during task recovery.

        BUG-5 FIX:
            Previously ``app_controller._restore_progress_from_disk`` directly
            mutated private attributes ``_batch_results`` and ``_batch_submitted``
            to restore state after a crash.  That violated the Law of Demeter and
            created hidden coupling.  This public method encapsulates the state
            mutation within the service that owns it.

        Args:
            batch_index: The 0-based index of the restored batch.
            part_path:   Absolute path to the already-assembled part file.
        """
        with self._state_lock:
            self._batch_results[batch_index] = part_path
            self._batch_submitted.add(batch_index)
        logging.info(f"Restored assembled batch part {batch_index} from disk: {part_path}")

    # ------------------------------------------------------------------
    # Final assembly
    # ------------------------------------------------------------------

    def assemble_final(
            self,
            output_path: Path,
            all_chunk_files: List[Optional[str]],
            speed: float,
            correlation_id: str
    ) -> None:
        """
        Performs the final assembly.

        This is a blocking call and must be run in a separate thread by the caller.
        """
        # Wait for any pending batch futures
        with self._state_lock:
            futures = list(self._futures)
        if futures:
            wait(futures)

        # Determine assembly strategy
        parts, use_fast = self._get_assembly_parts()

        try:
            if use_fast and parts:
                self._assemble_fast(parts, output_path, correlation_id)
            else:
                valid_files = [f for f in all_chunk_files if f is not None]
                if not valid_files:
                    raise Exception("No valid audio files to assemble")
                self._assemble_full(valid_files, output_path, speed, correlation_id)
        finally:
            # Cleanup intermediate part files
            if parts:
                for part in parts:
                    try:
                        p = Path(part)
                        if p.exists():
                            p.unlink()
                            logging.debug(f"Deleted temporary part: {part}")
                    except Exception as e:
                        logging.warning(f"Failed to delete temporary part {part}: {e}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_batch_size(self, batch_index: int) -> int:
        """Returns the number of chunks in the given batch."""
        start_idx = batch_index * self.BATCH_SIZE
        if start_idx >= self._chunks_count:
            return 0
        return min(self.BATCH_SIZE, self._chunks_count - start_idx)

    def _get_assembly_parts(self) -> Tuple[List[str], bool]:
        """Returns ordered list of batch part paths if all batches are complete."""
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
        set_correlation_id(correlation_id)
        logging.info("Fast assembly (copy codec from pre-assembled parts)")
        self._audio_assembler.assemble_audio(
            parts,
            str(output_path),
            speed=1.0,
            callback=self._progress_callback,
            copy_codec=True
        )

    def _assemble_full(
            self,
            files: List[str],
            output_path: Path,
            speed: float,
            correlation_id: str
    ) -> None:
        set_correlation_id(correlation_id)
        logging.info("Full assembly (re-encoding all chunks)")
        self._audio_assembler.assemble_audio(
            files,
            str(output_path),
            speed=speed,
            callback=self._progress_callback,
            copy_codec=False
        )

    def _progress_callback(self, percentage: float, remaining: float) -> None:
        self.assemblyProgressUpdated.emit(percentage, remaining)
