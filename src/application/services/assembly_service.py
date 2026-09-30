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
from src.constants import ASSEMBLY_BATCH_SIZE
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
          previous ``stop()`` always works reliably.
    """

    assemblyProgressUpdated = pyqtSignal(float, float)  # percentage, remaining
    batchAssemblyFinished = pyqtSignal(int, str)        # batch_index, file_path
    assemblyError = pyqtSignal(str)

    BATCH_SIZE = ASSEMBLY_BATCH_SIZE

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
        # Per batch: chunk indexes with audio / chunk indexes given up on. Sets, not counters,
        # so a chunk that fails and is later retried successfully is counted exactly once.
        self._batch_ready: Dict[int, Set[int]] = {}
        self._batch_failed: Dict[int, Set[int]] = {}

        self._state_lock = threading.Lock()
        self._chunks_count = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self, total_chunks: int) -> None:
        """
        Resets state for a new task and creates a fresh thread pool.

        Always creates a fresh ``ThreadPoolExecutor`` regardless of the previous
        executor state, so the reference is valid after ``reset()`` even following ``stop()``.
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
            self._batch_ready.clear()
            self._batch_failed.clear()
            self._chunks_count = total_chunks

            total_batches = (total_chunks + self.BATCH_SIZE - 1) // self.BATCH_SIZE
            for i in range(total_batches):
                self._batch_ready[i] = set()
                self._batch_failed[i] = set()
                self._batch_durations[i] = [0.0] * self._get_batch_size(i)

        # Create a fresh executor — always valid after reset
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="AssemblyWorker")

    def stop(self) -> None:
        """
        Cancels all running assembly tasks.

        Sets the executor to None so stale ``submit_batch_by_index`` calls recreate it
        safely; the next ``reset()`` creates a clean one.
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

        Safe to call for a chunk that previously failed (retry success) or that is
        reported more than once.

        Args:
            chunk_index: 0-based index of the chunk.
            duration: Duration of the generated chunk in seconds.

        Returns:
            The batch_index if every chunk in that batch is now resolved (ready or
            permanently failed) and the batch has not yet been submitted, else ``None``.
        """
        return self._resolve_chunk(chunk_index, ready=True, duration=duration)

    def mark_chunk_failed(self, chunk_index: int) -> Optional[int]:
        """
        Marks a chunk as permanently failed so the batch advances without hanging.

        A chunk that already has audio stays ready: a late failure report never
        discards a successful result.

        Args:
            chunk_index: 0-based index of the failed chunk.

        Returns:
            The batch_index if every chunk in that batch is now resolved and the batch
            has not yet been submitted, or ``None`` otherwise.
        """
        return self._resolve_chunk(chunk_index, ready=False, duration=0.0)

    def _resolve_chunk(self, chunk_index: int, ready: bool, duration: float) -> Optional[int]:
        batch_index = chunk_index // self.BATCH_SIZE
        rel_idx = chunk_index % self.BATCH_SIZE
        with self._state_lock:
            if batch_index not in self._batch_ready:
                return None

            ready_set = self._batch_ready[batch_index]
            failed_set = self._batch_failed[batch_index]
            if ready:
                failed_set.discard(rel_idx)
                ready_set.add(rel_idx)
                self._batch_durations[batch_index][rel_idx] = duration
            elif rel_idx not in ready_set:
                failed_set.add(rel_idx)
                self._batch_durations[batch_index][rel_idx] = 0.0

            resolved = len(ready_set | failed_set)
            if resolved == self._get_batch_size(batch_index) and batch_index not in self._batch_submitted:
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
        tmp_part_path = Path(output_dir) / f"part_{batch_index}_tmp.mp3"
        logging.info(f"Submitting batch {batch_index} for assembly ({len(files)} files)")

        # Verify all chunk files in this batch are valid
        if any(f is None or not Path(f).exists() or Path(f).stat().st_size == 0 for f in files):
            logging.warning(
                f"Batch {batch_index} contains missing or empty chunk file(s) — "
                "deferring batch assembly until all chunks are synthesized."
            )
            with self._state_lock:
                self._batch_submitted.discard(batch_index)
            return

        valid_files = list(files)
        valid_durations = list(durations)

        def _assemble_task() -> str:
            set_correlation_id(correlation_id)
            try:
                self._concat(valid_files, str(tmp_part_path), speed, valid_durations)
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
                if result_path:
                    with self._state_lock:
                        self._batch_results[batch_index] = result_path
                    self.batchAssemblyFinished.emit(batch_index, result_path)
            except Exception as e:
                err_str = str(e)
                if "cancelled" in err_str or "terminated" in err_str or self._executor is None:
                    logging.info(f"Batch {batch_index} assembly cancelled or stopped.")
                else:
                    self.assemblyError.emit(f"Batch {batch_index} failed: {err_str}")

        future.add_done_callback(_done_callback)

    # ------------------------------------------------------------------
    # Recovery public API
    # ------------------------------------------------------------------

    def restore_batch_result(self, batch_index: int, part_path: str) -> None:
        """
        Restores a previously assembled batch part during task recovery.

        Callers restore batch state through this method instead of touching the
        service's private attributes.

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

        # Strictly enforce complete chunk files
        missing = [i for i, f in enumerate(all_chunk_files) if f is None or not Path(f).exists() or Path(f).stat().st_size == 0]
        if missing:
            raise ValueError(
                f"Cannot assemble final audio: {len(missing)} chunk(s) missing or empty out of {len(all_chunk_files)}. "
                f"Missing chunk numbers: {[i + 1 for i in missing[:10]]}"
            )

        # Determine assembly strategy
        parts, use_fast = self._get_assembly_parts()

        try:
            if use_fast and parts:
                self._assemble_fast(parts, output_path, correlation_id)
            else:
                self._assemble_full(all_chunk_files, output_path, speed, correlation_id)
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

    @staticmethod
    def _needs_reencode(speed: float) -> bool:
        return abs(speed - 1.0) > 0.01

    def _concat(
            self,
            files: List[str],
            output_path: str,
            speed: float,
            durations: Optional[List[float]] = None,
            callback=None,
    ) -> None:
        """
        Joins chunk MP3s. Chunks are already MP3, so without a speed change they are copied
        frame-for-frame (no second lossy encode). Only a speed change, or a copy that ffmpeg
        rejects (e.g. chunks with mismatching parameters), falls back to re-encoding.
        """
        kwargs = {"durations": durations} if durations is not None else {}
        if callback is not None:
            kwargs["callback"] = callback
        if self._needs_reencode(speed):
            self._audio_assembler.assemble_audio(
                files, output_path, speed=speed, copy_codec=False, **kwargs
            )
            return
        try:
            self._audio_assembler.assemble_audio(
                files, output_path, speed=1.0, copy_codec=True, **kwargs
            )
        except Exception as exc:
            text = str(exc).lower()
            if "cancelled" in text or "terminated" in text or "stopped" in text:
                raise
            logging.warning("Stream copy failed (%s); re-encoding instead.", exc)
            self._audio_assembler.assemble_audio(
                files, output_path, speed=1.0, copy_codec=False, **kwargs
            )

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
        self._concat(files, str(output_path), speed, callback=self._progress_callback)

    def _progress_callback(self, percentage: float, remaining: float) -> None:
        self.assemblyProgressUpdated.emit(percentage, remaining)
