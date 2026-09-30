"""
Application controller.

This module orchestrates the entire application, coordinating between GUI, domain, and infrastructure layers.
"""

import logging
import time
import re
import tempfile
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Set

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QThread

from src.domain.models import ProjectConfig, AudioChunk, GenerationTask, TaskStatus, TtsBackend
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.domain.audio_utils import get_mp3_duration
from src.infrastructure.disk_space import ensure_free_space
from src.domain.tts_generator_protocol import TtsGeneratorProtocol
import os
from src.infrastructure.file_manager import FileManager
from src.application.services.queue_service import QueueService
from src.application.services.generation_service import GenerationService
from src.application.services.assembly_service import AssemblyService
from src.application.services.persistence_service import PersistenceService
from src.application.services.piper_setup_service import PiperSetupService
from src.application.services.xtts_setup_service import XttsSetupService
from src.application.progress_restorer import ProgressRestorer, load_chunks
from src.application.backend_coordinator import BackendCoordinator
from src.constants import COMPLETION_POLL_MS, MAX_RETRY_SWEEPS, PROGRESS_REFRESH_MS, RETRY_SWEEP_DELAY_MS
from src.application.preparation_worker import PreparationWorker  # noqa: F401  (re-exported)
from src.infrastructure.logging_config import set_correlation_id




class _BackendBridge(QObject):
    """Carries results of background backend operations back to the GUI thread."""

    finished = pyqtSignal(object, object)  # (callback, argument)


class ApplicationController(QObject):
    """
    Central controller that orchestrates audio generation workflow.
    Delegates complex logic to specialized services.
    """

    progressUpdated = pyqtSignal(int, int, str, float) # processed, total, eta_str, chunks_per_sec
    assemblyProgressUpdated = pyqtSignal(float, float)
    errorOccurred = pyqtSignal(str)
    globalProgressUpdated = pyqtSignal(float, str)  # percentage, eta_string

    taskAdded = pyqtSignal(GenerationTask)
    taskUpdated = pyqtSignal(GenerationTask)
    queueStatusChanged = pyqtSignal(bool)

    def __init__(
            self,
            queue_service: QueueService,
            text_processor: TextProcessor,
            text_chunker: TextChunker,
            file_manager: FileManager,
            generation_service: GenerationService,
            assembly_service: AssemblyService,
            persistence_service: PersistenceService,
            audio_generator_resolver=None,
            piper_setup_service: Optional[PiperSetupService] = None,
            xtts_setup_service: Optional[XttsSetupService] = None,
    ) -> None:
        """Initialize the ApplicationController."""
        super().__init__()

        self._queue_service = queue_service
        self._text_processor = text_processor
        self._text_chunker = text_chunker
        self._file_manager = file_manager

        # Injected Services
        self._generation_service = generation_service
        self._assembly_service = assembly_service
        self._persistence_service = persistence_service
        self._backends = BackendCoordinator(piper_setup_service, xtts_setup_service)
        self._shutdown_done = False

        # Docker / GPU operations never run on the GUI thread. The runner executes
        # ``fn`` in the background and delivers ``on_done(result)`` / ``on_error(exc)``
        # on the GUI thread; tests inject a synchronous runner.
        self._bridge = _BackendBridge()
        self._bridge.finished.connect(self._deliver_backend_result)
        self._backend_runner: Callable[[Callable[[], Any], Callable[[Any], None], Callable[[Exception], None]], None] = \
            self._run_backend_in_thread
        self._release_runner: Callable[[Callable[[], None]], None] = self._run_release_in_thread

        # Callable (backend: TtsBackend) -> TtsGeneratorProtocol
        # Allows dynamic switching of the TTS generator per task.
        self._audio_generator_resolver = audio_generator_resolver


        self._prep_worker: Optional[PreparationWorker] = None

        self._text_dir: Optional[str] = None
        self._audio_dir: Optional[str] = None
        self._last_manifest_write: float = float('-inf')
        # Stateless on purpose: restoring progress must not depend on which generator was last active.
        self._duration_reader: Callable[[str], float] = get_mp3_duration
        self._chunks: List[AudioChunk] = []
        self._audio_files: List[Optional[str]] = []

        self._is_stopped = False
        self._retry_sweep_count = 0
        self._retry_in_progress = False
        self._last_save_time = 0.0

        self._setup_service_subscriptions()
        self._setup_internal_connections()

        self._progress_timer = QTimer()
        self._progress_timer.timeout.connect(self._update_progress_display)
        self._last_signal_time = 0
        self._signal_throttle_ms = 100

        self._completion_monitor_timer: Optional[QTimer] = None

    @property
    def _piper_setup_service(self) -> Optional[PiperSetupService]:
        return self._backends.service(TtsBackend.PIPER)

    @_piper_setup_service.setter
    def _piper_setup_service(self, service: Optional[PiperSetupService]) -> None:
        self._backends.set_service(TtsBackend.PIPER, service)

    @property
    def _xtts_setup_service(self) -> Optional[XttsSetupService]:
        return self._backends.service(TtsBackend.XTTS)

    @_xtts_setup_service.setter
    def _xtts_setup_service(self, service: Optional[XttsSetupService]) -> None:
        self._backends.set_service(TtsBackend.XTTS, service)

    @property
    def _piper_used(self) -> bool:
        return TtsBackend.PIPER in self._backends.used

    @_piper_used.setter
    def _piper_used(self, value: bool) -> None:
        (self._backends.used.add if value else self._backends.used.discard)(TtsBackend.PIPER)

    @property
    def _backends_needed(self) -> Set[TtsBackend]:
        return self._backends.needed

    def _setup_service_subscriptions(self) -> None:
        """Sets up subscriptions to queue service events."""
        self._queue_service.subscribe_task_added(self.taskAdded.emit)
        self._queue_service.subscribe_task_added(lambda task: self._save_state(force=True))
        self._queue_service.subscribe_task_updated(self.taskUpdated.emit)
        self._queue_service.subscribe_task_updated(lambda task: self._save_state(force=False))
        self._queue_service.subscribe_status_changed(self.queueStatusChanged.emit)


    def _setup_internal_connections(self) -> None:
        """Sets up connections from generation and assembly services."""
        # Generation Service signals
        self._generation_service.chunkGenerated.connect(self._on_chunk_generated)
        self._generation_service.chunkFailed.connect(self._on_chunk_failed)
        self._generation_service.batchFailed.connect(self._on_batch_failed)
        self._generation_service.errorOccurred.connect(self.errorOccurred.emit)
        self._generation_service.generationAborted.connect(self._on_generation_aborted)
        
        # Assembly Service signals
        self._assembly_service.assemblyProgressUpdated.connect(self._on_assembly_progress)
        self._assembly_service.assemblyError.connect(self._on_assembly_error)
        # We can also listen to batchAssemblyFinished if needed for logging

    def add_task(self, config: ProjectConfig) -> None:
        """Adds a new task to the queue."""
        self._queue_service.add_task(config)
        self._emit_global_progress()
        self._process_queue()

    def _process_queue(self) -> None:
        """Processes the next task in the queue if idle."""
        task = self._queue_service.get_next_task()
        if task:
            self._start_task(task)

    def get_current_task(self) -> Optional[GenerationTask]:
        """Returns the current task being processed."""
        return self._get_current_task()

    def _get_current_task(self) -> Optional[GenerationTask]:
        """Returns the current task being processed."""
        return self._queue_service.get_current_task()

    def _start_task(self, task: GenerationTask) -> bool:
        """Starts the audio generation process for a specific task."""
        try:
            set_correlation_id(str(task.id))
            
            logging.info(
                "Starting task %s: project='%s', backend='%s', lang='%s', gender='%s', threads=%d, input='%s'",
                task.id, task.config.project_name, task.config.tts_backend.value,
                task.config.language, task.config.gender, task.config.thread_count,
                task.config.input_file_path,
            )

            self._is_stopped = False
            self.progressUpdated.emit(0, 0, "Preparing...", 0.0)

            if task.text_dir and task.audio_dir and os.path.isdir(task.text_dir) and os.path.isdir(task.audio_dir):
                logging.info(f"Restoring task progress from disk directories: {task.text_dir}, {task.audio_dir}")
                restored = self._restore_progress_from_disk(task)
                if restored:
                    return True
                logging.warning("Failed to restore progress from disk, falling back to clean preparation.")

            self._initialize_task_state(task)
            self._start_preparation_worker(task)
            return True
        except Exception as e:
            logging.error(f"Failed to start task {task.id}: {e}", exc_info=True)
            self._handle_task_failure(f"Failed to start: {str(e)}")
            return False

    def _initialize_task_state(self, task: GenerationTask) -> None:
        """Initializes state for a new task."""
        self._is_stopped = False
        self._queue_service.update_task_status(
            TaskStatus.PROCESSING,
            "Preparing..."
        )

        self._chunks = []
        self._audio_files = []
        self._text_dir = None
        self._audio_dir = None
        self._retry_sweep_count = 0
        self._retry_in_progress = False

        self.progressUpdated.emit(0, 0, "Preparing...", 0.0)

    def _start_preparation_worker(self, task: GenerationTask) -> None:
        """Starts the preparation worker thread."""
        self._prep_worker = PreparationWorker(
            str(task.id),
            task.config,
            self._file_manager,
            self._text_processor,
            self._text_chunker
        )
        self._prep_worker.finished.connect(self._on_preparation_finished)
        self._prep_worker.error.connect(self._on_preparation_error)
        self._prep_worker.start()

    def _on_preparation_finished(
            self,
            chunks: List[AudioChunk],
            text_dir: str,
            audio_dir: str
    ) -> None:
        """Called when preparation worker completes successfullly."""
        try:
            if self._is_stopped:
                return

            self._chunks = chunks
            self._text_dir = text_dir
            self._audio_dir = audio_dir
            self._last_manifest_write = float('-inf')
            self._audio_files = [None] * len(chunks)

            # Reset assembly service
            self._assembly_service.reset(len(chunks))

            task = self._get_current_task()
            if task:
                task.text_dir = text_dir
                task.audio_dir = audio_dir
                self._save_state(force=True)

            logging.info(f"Preparation finished for task {task.id if task else 'unknown'}. Chunks: {len(chunks)}")
            self._start_generation_process()
        except Exception as e:
            logging.error(f"Error after preparation: {e}", exc_info=True)
            self.errorOccurred.emit(f"System error after preparation: {e}")

    def _on_preparation_error(self, error_msg: str) -> None:
        """Called when preparation worker fails."""
        logging.error(f"Preparation worker error: {error_msg}")
        self.errorOccurred.emit(f"Preparation failed: {error_msg}")
        self._handle_task_failure(error_msg)

    def _start_generation_process(self) -> None:
        """
        Prepares the task's TTS backend (off the GUI thread) and then starts generation.

        Local backends are checked fail-fast: if Docker/Piper/XTTS cannot work, the task
        fails at once with the real reason instead of retrying every chunk.
        """
        task = self._get_current_task()
        if not task:
            return

        # Switch the TTS generator to match the task's configured backend.
        # This must happen before start_generation() is called.
        if self._audio_generator_resolver is not None:
            generator = self._audio_generator_resolver(task.config.tts_backend)
            self._generation_service.switch_generator(generator)
            logging.info(
                "Switched TTS generator to '%s' (backend='%s') for task %s",
                type(generator).__name__, task.config.tts_backend.value, task.id,
            )

        backend = task.config.tts_backend
        prepare = self._make_backend_preparation(task)
        if prepare is None:
            self._begin_generation(task)
            return

        token = self._backends.begin(backend)
        label = self._backends.label(backend)
        self._queue_service.update_task_status(TaskStatus.PROCESSING, f"Preparing {label}...")

        def on_done(_result: Any) -> None:
            if self._is_prep_current(token, task):
                self._begin_generation(task)

        def on_error(exc: Exception) -> None:
            if not self._is_prep_current(token, task):
                return
            reason = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            logging.error("%s is not ready for task %s: %s", label, task.id, reason)
            self._handle_task_failure(f"{label} is not ready: {reason}")

        self._backend_runner(prepare, on_done, on_error)

    def _make_backend_preparation(self, task: GenerationTask) -> Optional[Callable[[], Any]]:
        """Returns the blocking readiness routine for the task's backend, or None if none is needed."""
        cfg = task.config
        return self._backends.prepare_callable(cfg.tts_backend, cfg.language, cfg.gender)

    def _is_prep_current(self, token: int, task: GenerationTask) -> bool:
        current = self._get_current_task()
        return (
            self._backends.is_current(token)
            and not self._is_stopped
            and current is not None
            and current.id == task.id
        )

    def _invalidate_backend_prep(self) -> None:
        """Makes any in-flight backend preparation ignore its result."""
        self._backends.invalidate()

    def _begin_generation(self, task: GenerationTask) -> None:
        """Starts the GenerationService once the backend is known to be ready."""
        logging.info(
            "Starting generation: task %s, backend=%s, lang=%s",
            task.id, task.config.tts_backend.value, task.config.language,
        )

        self._generation_service.start_generation(
            task,
            self._chunks,
            self._audio_dir
        )

        self._progress_timer.start(PROGRESS_REFRESH_MS)
        self._monitor_completion()

    def _on_generation_aborted(self, reason: str) -> None:
        """A whole batch failed for a reason no retry can fix: fail the task with the real cause."""
        if self._is_stopped or self._get_current_task() is None:
            return
        self._stop_completion_monitor()
        self._progress_timer.stop()
        self._handle_task_failure(f"Generation aborted: {reason}")

    def _run_backend_in_thread(self, fn, on_done, on_error) -> None:
        def target() -> None:
            try:
                result = fn()
            except Exception as exc:
                self._bridge.finished.emit(on_error, exc)
            else:
                self._bridge.finished.emit(on_done, result)

        threading.Thread(target=target, name="backend-prep", daemon=True).start()

    def _deliver_backend_result(self, callback: Any, argument: Any) -> None:
        callback(argument)

    def _run_release_in_thread(self, fn) -> None:
        threading.Thread(target=fn, name="backend-release", daemon=True).start()

    def _update_progress_display(self) -> None:
        """Updates the progress display via signal."""
        current_time = time.time() * 1000

        if (current_time - self._last_signal_time) < self._signal_throttle_ms:
            return

        self._last_signal_time = current_time
        processed, total, eta, speed = self._generation_service.get_progress_info()
        
        self.progressUpdated.emit(processed, total, eta, speed)

        # Update Task Progress (Cap at 90%)
        raw_percentage = self._generation_service.get_progress_percentage()
        capped_percentage = min(raw_percentage, 90.0)
        self._queue_service.update_task_progress(capped_percentage)

        self._emit_global_progress()

    def _emit_global_progress(self) -> None:
        """Calculates and emits global progress for all tasks in the list."""
        all_tasks = self._queue_service.get_all_tasks()
        if not all_tasks:
            self.globalProgressUpdated.emit(100.0, "00:00:00")
            return

        total_tasks = len(all_tasks)
        total_progress = sum(t.progress for t in all_tasks)
        global_percentage = total_progress / total_tasks

        # Calculate Global ETA
        # We use current task's ETA and estimate others
        processed, total, eta_str, speed = self._generation_service.get_progress_info()
        
        # Parse current ETA string (HH:MM:SS) to seconds
        current_eta_seconds = 0
        if eta_str and eta_str != "...":
            try:
                parts = eta_str.split(':')
                if len(parts) == 3:
                    current_eta_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                pass

        # For pending tasks, we don't have exact chunk counts yet.
        # Simple heuristic: assume each pending task takes same time as the average of the current one if it were full.
        # A better heuristic would be based on file size, but let's keep it simple for now.
        estimated_time_per_task = 0
        if total > 0:
            if processed > 0 and total > processed:
                estimated_time_per_task = (current_eta_seconds / (total - processed)) * total
            else:
                # Fallback estimate: 1.5 second per chunk if no progress yet
                estimated_time_per_task = total * 1.5
            
        pending_tasks_count = len([t for t in all_tasks if t.status == TaskStatus.PENDING])
        global_eta_seconds = current_eta_seconds + (pending_tasks_count * estimated_time_per_task)

        # Format global ETA
        gh = int(global_eta_seconds // 3600)
        gm = int((global_eta_seconds % 3600) // 60)
        gs = int(global_eta_seconds % 60)
        global_eta_str = f"{gh:02d}:{gm:02d}:{gs:02d}"

        self.globalProgressUpdated.emit(global_percentage, global_eta_str)

    def _on_chunk_generated(self, chunk_number: int, file_path: str, duration: float) -> None:
        """Callback when a chunk is successfully generated."""
        if self._is_stopped:
            return
            
        # Update audio files list (chunk_number is 1-based)
        if 1 <= chunk_number <= len(self._audio_files):
            self._audio_files[chunk_number - 1] = file_path
            
            # Optimized batch tracking
            batch_index = self._assembly_service.mark_chunk_ready(chunk_number - 1, duration)
            if batch_index is not None:
                task = self._get_current_task()
                if task and self._audio_dir:
                    start_idx = batch_index * self._assembly_service.BATCH_SIZE
                    end_idx = min(start_idx + self._assembly_service.BATCH_SIZE, len(self._audio_files))
                    batch_files = self._audio_files[start_idx:end_idx]
                    
                    self._assembly_service.submit_batch_by_index(
                        batch_index,
                        batch_files,
                        self._audio_dir,
                        task.config.speed,
                        str(task.id)
                    )
            self._update_task_manifest(force=batch_index is not None)

    def _on_chunk_failed(self, chunk_number: int, error_msg: str) -> None:
        """Callback when a chunk fails permanently after retries."""
        if self._is_stopped:
            return
        logging.warning(f"Chunk {chunk_number} failed permanently: {error_msg}")
        if 1 <= chunk_number <= len(self._audio_files):
            batch_index = self._assembly_service.mark_chunk_failed(chunk_number - 1)
            if batch_index is not None:
                task = self._get_current_task()
                if task and self._audio_dir:
                    start_idx = batch_index * self._assembly_service.BATCH_SIZE
                    end_idx = min(start_idx + self._assembly_service.BATCH_SIZE, len(self._audio_files))
                    batch_files = self._audio_files[start_idx:end_idx]
                    self._assembly_service.submit_batch_by_index(
                        batch_index,
                        batch_files,
                        self._audio_dir,
                        task.config.speed,
                        str(task.id)
                    )
            self._update_task_manifest(force=batch_index is not None)

    MANIFEST_MIN_INTERVAL_SECONDS = 2.0

    def _update_task_manifest(self, force: bool = False) -> None:
        """
        Saves a progress checkpoint to manifest.json.

        Rewriting the file after every chunk makes the total I/O quadratic for big books,
        so writes are throttled. ``force`` is used at moments that matter (batch complete,
        task failure). The chunk and audio files on disk stay the source of truth on restore.
        """
        task = self._get_current_task()
        if not task or not self._audio_dir:
            return
        now = time.monotonic()
        if not force and (now - self._last_manifest_write) < self.MANIFEST_MIN_INTERVAL_SECONDS:
            return
        manifest_data = {
            "task_id": str(task.id),
            "updated_at": datetime.now().isoformat(),
            "total_chunks": len(self._chunks),
            "completed_chunks": [i + 1 for i, f in enumerate(self._audio_files) if f is not None],
        }
        if self._persistence_service.save_manifest(self._audio_dir, manifest_data):
            self._last_manifest_write = now

    def _on_batch_failed(self, batch: List[AudioChunk], error_msg: str) -> None:
        """
        Callback when a batch fails after per-chunk retries.
        Logs error and prepares for task-level retry sweeps.
        """
        logging.error(
            f"Batch generation failed ({len(batch)} chunk(s) failed: {error_msg}). "
            "Unsynthesized chunks will be queued for task retry sweep."
        )
        valid_chunks_count = sum(1 for f in self._audio_files if f is not None)
        if valid_chunks_count == 0 and len(self._chunks) > 0 and self._retry_sweep_count >= 3:
            logging.error("All chunks in task failed permanently after retry sweeps — failing task.")
            self._handle_task_failure(f"Task synthesis error: {error_msg}")

    MAX_RETRY_SWEEPS = MAX_RETRY_SWEEPS
    RETRY_SWEEP_DELAY_MS = RETRY_SWEEP_DELAY_MS

    def _monitor_completion(self) -> None:
        """
        Monitors generation completion, triggers retry sweeps for missing chunks if needed,
        and starts final assembly only when 100% complete.
        """
        if self._completion_monitor_timer is not None:
            self._completion_monitor_timer.stop()
            self._completion_monitor_timer = None

        self._completion_monitor_timer = QTimer()
        self._completion_monitor_timer.timeout.connect(self._check_completion)
        self._completion_monitor_timer.start(COMPLETION_POLL_MS)

    def _stop_completion_monitor(self) -> None:
        if self._completion_monitor_timer:
            self._completion_monitor_timer.stop()

    def _find_missing_chunks(self) -> List[AudioChunk]:
        """Returns chunks whose audio file is unknown, absent or empty."""
        return [
            self._chunks[i] for i, f in enumerate(self._audio_files)
            if f is None or not Path(f).exists() or Path(f).stat().st_size == 0
        ]

    def _check_completion(self) -> None:
        """One tick of the completion monitor."""
        if self._is_stopped:
            self._stop_completion_monitor()
            return

        # A sweep is scheduled but has not been submitted yet.
        if self._retry_in_progress:
            return

        processed, total, _, _ = self._generation_service.get_progress_info()
        if total <= 0 or processed < total:
            return

        missing_chunks = self._find_missing_chunks()
        if not missing_chunks:
            self._stop_completion_monitor()
            self._progress_timer.stop()
            self._finalize_generation()
            return

        if self._retry_sweep_count >= self.MAX_RETRY_SWEEPS:
            self._stop_completion_monitor()
            self._progress_timer.stop()
            error_msg = (
                f"Generation failed: {len(missing_chunks)} chunk(s) could not be synthesized "
                f"after {self.MAX_RETRY_SWEEPS} retry sweeps. Refusing to produce incomplete audio file."
            )
            logging.error(error_msg)
            self._handle_task_failure(error_msg)
            return

        task = self._get_current_task()
        if not (task and self._audio_dir):
            return

        logging.warning(
            f"Initial pass complete, but {len(missing_chunks)} chunk(s) are missing. "
            f"Scheduling retry sweep after {self.RETRY_SWEEP_DELAY_MS // 1000}s cooling pause..."
        )
        self._retry_in_progress = True
        QTimer.singleShot(self.RETRY_SWEEP_DELAY_MS, lambda: self._execute_retry_sweep(task))

    def _execute_retry_sweep(self, task: GenerationTask) -> None:
        """
        Submits the missing chunks for another synthesis attempt.

        The missing list is recomputed here because chunk results are delivered to the
        GUI thread asynchronously: what looked missing when the sweep was scheduled may
        have arrived during the cooling pause. ``GenerationService.retry_chunks`` marks
        the chunks as in flight again synchronously, so the monitor does not see a
        finished generation until the sweep has really reported back.
        """
        if self._get_current_task() is not task:
            return
        try:
            if self._is_stopped:
                return
            missing_chunks = self._find_missing_chunks()
            if not missing_chunks:
                return
            self._retry_sweep_count += 1
            logging.warning(
                f"Retry sweep {self._retry_sweep_count}/{self.MAX_RETRY_SWEEPS} "
                f"for {len(missing_chunks)} chunk(s)."
            )
            self._generation_service.retry_chunks(task, missing_chunks, self._audio_dir)
        except Exception as e:
            logging.error(f"Retry sweep failed to start: {e}", exc_info=True)
        finally:
            self._retry_in_progress = False

    def _finalize_generation(self) -> None:
        """Finalizes generation by assembling audio and cleaning up."""
        task = self._get_current_task()
        if not task:
            return

        missing_indices = [
            i for i, f in enumerate(self._audio_files)
            if f is None or not Path(f).exists() or Path(f).stat().st_size == 0
        ]
        if missing_indices:
            missing_nums = [i + 1 for i in missing_indices[:10]]
            error_msg = (
                f"Generation failed: {len(missing_indices)} chunk(s) missing or empty "
                f"(e.g., chunk numbers {missing_nums}). Aborting assembly to prevent outputting incomplete audio."
            )
            logging.error(error_msg)
            self._handle_task_failure(error_msg)
            return

        logging.info("Finalizing generation. Starting final assembly...")
        try:
            output_path = self._prepare_output_path(task)
            
            # Start assembly in a background thread to not block UI
            thread = threading.Thread(
                target=self._execute_assembly_workflow,
                args=(output_path, task.config.speed, str(task.id)),
                daemon=True
            )
            thread.start()
            
        except Exception as e:
            logging.error(f"Failed to prepare final generation: {e}")
            self.errorOccurred.emit(f"Failed to prepare final generation: {str(e)}")

    def _execute_assembly_workflow(self, output_path: Path, speed: float, correlation_id: str) -> None:
        """
        Core assembly workflow executed in a separate daemon thread.

        The result is handed to the GUI thread, which ignores it if the pipeline was
        stopped while assembly was running.
        """
        error: Optional[Exception] = None
        try:
            self._assembly_service.assemble_final(
                output_path,
                self._audio_files,
                speed,
                correlation_id
            )
        except Exception as e:
            error = e
        # Queue/timer/thread objects belong to the GUI thread: hand the outcome over instead
        # of touching them from this worker.
        self._post_to_gui(lambda: self._on_assembly_finished(correlation_id, error))

    def _post_to_gui(self, fn: Callable[[], None]) -> None:
        """Runs ``fn`` on the GUI thread (queued through the bridge when called from elsewhere)."""
        self._bridge.finished.emit(lambda _unused: fn(), None)

    def _on_assembly_finished(self, task_id: str, error: Optional[Exception]) -> None:
        """GUI-thread continuation of the assembly worker."""
        current = self._get_current_task()
        if self._is_stopped or current is None or str(current.id) != task_id:
            # Stopped, cancelled, or superseded by another task while ffmpeg ran.
            if error is not None:
                logging.info(f"Assembly error ignored (pipeline stopped): {error}")
            else:
                logging.info("Assembly completed but pipeline was stopped — skipping task completion.")
            return
        if error is not None:
            self._handle_assembly_error(error)
            return
        self._cleanup_temp_files()
        self._mark_task_completed()

    def _on_assembly_progress(self, percentage: float, remaining: float) -> None:
        """Callback for assembly progress signals."""
        # Convert 0-100% to 90-100%
        final_percentage = 90.0 + (percentage * 0.1)
        self._queue_service.update_task_progress(final_percentage)
        self.assemblyProgressUpdated.emit(percentage, remaining)

    def _on_assembly_error(self, error_msg: str) -> None:
        if "cancelled" in error_msg.lower() or "terminated" in error_msg.lower() or self._is_stopped:
            logging.info(f"Assembly cancelled: {error_msg}")
        else:
            logging.error(f"Assembly error: {error_msg}")

    def pause_generation(self, task_id: Optional[str] = None) -> None:
        """Pauses the ongoing audio generation."""
        current_task = self._get_current_task()
        if task_id is None and current_task:
            task_id = str(current_task.id)

        if not task_id:
            return

        if current_task and str(current_task.id) == task_id:
            if current_task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.STOPPED):
                if not self._generation_service.has_worker_pool or current_task.status in (TaskStatus.FAILED, TaskStatus.STOPPED):
                    current_task.update_status(TaskStatus.PENDING, "Resuming...")
                    self._start_task(current_task)
                    return

            is_paused = self._generation_service.pause()
            if is_paused:
                self._queue_service.update_task_status(TaskStatus.PAUSED, "Paused")
                logging.info(f"Generation paused for task {task_id}")
            else:
                self._queue_service.update_task_status(TaskStatus.PROCESSING, "Resumed")
                logging.info(f"Generation resumed for task {task_id}")
        else:
            updated_task = self._queue_service.toggle_task_pause(task_id)
            if updated_task:
                logging.info(f"Task {task_id} pause status toggled to: {updated_task.status.value}")
                self._save_state(force=True)
                if updated_task.status == TaskStatus.PENDING:
                    self._process_queue()

    def retry_generation(self, task_id: Optional[str] = None) -> None:
        """Retries audio generation for a failed or stopped task."""
        current_task = self._get_current_task()
        if task_id is None and current_task:
            task_id = str(current_task.id)

        if not task_id:
            return

        logging.info(f"Initiating retry for task {task_id}...")
        if current_task and str(current_task.id) == task_id:
            self._invalidate_backend_prep()
            self._generation_service.stop()

        updated_task = self._queue_service.retry_task(task_id)
        if updated_task:
            logging.info(f"Task {task_id} set to PENDING for retry.")
            self._save_state(force=True)
            self._process_queue()

    def shutdown(self) -> None:
        """Gracefully stops ongoing processing and saves queue state for next run."""
        if self._shutdown_done:
            return
        self._shutdown_done = True
        logging.info("Shutting down ApplicationController...")
        self._is_stopped = True
        self._invalidate_backend_prep()
        self._terminate_prep_worker()
        self._cleanup_active_resources()
        self._generation_service.stop(wait=True)
        self._assembly_service.stop()
        
        current_task = self._get_current_task()
        if current_task and current_task.status in [TaskStatus.PROCESSING, TaskStatus.PAUSED]:
            current_task.status = TaskStatus.PAUSED
            current_task.message = "Paused"
            
        self._save_state(force=True)
        self._backends.shutdown()

    def _has_unfinished_task_for(self, backend: TtsBackend) -> bool:
        """True if a PENDING/PROCESSING task still needs the given backend."""
        return any(
            t.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
            and t.config.tts_backend == backend
            for t in self._queue_service.get_all_tasks()
        )

    def _release_idle_backends(self) -> None:
        """
        Frees local-engine resources that no remaining task needs.

        The decision is made on the GUI thread; the slow part (``docker compose down``,
        unloading the model) runs in the background so the window never freezes. Piper's
        container is stopped only if this app run actually started a Piper task.
        """
        self._backends.release_idle(self._has_unfinished_task_for, self._release_runner)

    def stop_generation(self) -> None:
        """Stops the audio generation and discards every task (explicit "clear queue")."""
        self._is_stopped = True
        logging.info("Stopping all generation tasks...")

        self._invalidate_backend_prep()
        self._terminate_prep_worker()
        self._generation_service.stop()
        self._assembly_service.stop()

        discarded_tasks = self._queue_service.get_all_tasks()
        self._queue_service.clear_all_tasks()
        self._cleanup_temp_files()
        for task in discarded_tasks:
            self._cleanup_task_dirs(task)
        self._save_state(force=True)
        self._finalize_task()

    def cancel_current_task(self) -> None:
        """Cancels only the running task; the rest of the queue keeps going."""
        task = self._get_current_task()
        logging.info("Cancelling current task %s...", task.id if task else None)
        self._is_stopped = True

        self._invalidate_backend_prep()
        self._terminate_prep_worker()
        self._generation_service.stop()
        self._assembly_service.stop()

        self._cleanup_temp_files()
        if task:
            self._cleanup_task_dirs(task)
        self._queue_service.discard_current_task()
        self._finalize_task()

    def cancel_task(self, task_id: str) -> None:
        """Cancels a specific task by its ID (running, queued, failed or stopped)."""
        current_task = self._get_current_task()
        if current_task and str(current_task.id) == task_id:
            self.cancel_current_task()
        else:
            logging.info(f"Cancelling queued task: {task_id}")
            task = self._queue_service.find_task(task_id)
            self._queue_service.remove_task(task_id)
            if task:
                self._cleanup_task_dirs(task)
            self._save_state(force=True)
            self._emit_global_progress()

    def _handle_task_failure(self, error_msg: str) -> None:
        """
        Marks the current task as failed and moves on to the next one.

        The failed task stays available for Retry (its temporary files are kept so a retry
        resumes from the chunks already synthesized); they are removed when the user deletes it.
        """
        self._is_stopped = True
        self._update_task_manifest(force=True)
        self._queue_service.update_task_status(TaskStatus.FAILED, error_msg)
        self.errorOccurred.emit(error_msg)
        # Keep in-flight workers of the dead task from reporting into the next one.
        self._generation_service.stop()
        self._finalize_task()

    def _finalize_task(self) -> None:
        """Clean up current task and process next."""
        self._queue_service.finalize_current_task()
        self._cleanup_active_resources()
        self._save_state(force=True)
        self._process_queue()
        self._emit_global_progress()
        self._release_idle_backends()

    def _save_state(self, force: bool = False) -> None:
        """Saves current queue state to persistence with 3-second throttling unless forced."""
        import time
        now = time.time()
        if not force and (now - self._last_save_time) < 3.0:
            return

        try:
            tasks = self._queue_service.get_all_tasks()
            if self._persistence_service.save_state(tasks):
                self._last_save_time = now
        except Exception as e:
            logging.error(f"Failed to auto-save state: {e}", exc_info=True)

    def restore_state(self) -> None:
        """Restores queue state from persistence."""
        try:
            tasks = self._persistence_service.load_state()
            if not tasks:
                return

            logging.info(f"Restoring {len(tasks)} tasks from saved state.")
            
            failed_tasks = [t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.STOPPED)]
            tasks = [t for t in tasks if t not in failed_tasks]

            if tasks and tasks[0].status in [TaskStatus.PROCESSING, TaskStatus.PAUSED]:
                first_task = tasks[0]
                first_task.status = TaskStatus.PAUSED
                first_task.message = "Paused"
                self._queue_service.restore_current_task(first_task)
                self.taskAdded.emit(first_task)
                self.taskUpdated.emit(first_task)
                pending_tasks = tasks[1:]
            else:
                pending_tasks = tasks

            for task in pending_tasks:
                task.status = TaskStatus.PENDING
                task.message = "Waiting..."
                self._queue_service.restore_pending_task(task)
                self.taskAdded.emit(task)

            for task in failed_tasks:
                self._queue_service.restore_failed_task(task)
                self.taskAdded.emit(task)

            self._emit_global_progress()
            # Restored pending tasks must run without waiting for the user to add a new one.
            self._process_queue()
        except Exception as e:
            logging.error(f"Error restoring state: {e}", exc_info=True)

    def _restore_progress_from_disk(self, task: GenerationTask) -> bool:
        """Attempts to restore task chunks and progress from physical files on disk."""
        try:
            chunks = load_chunks(task.text_dir)
            if not chunks:
                return False
            restored = ProgressRestorer(self._assembly_service, self._duration_reader).restore(
                chunks, task.audio_dir, task.config.speed, str(task.id)
            )
            self._chunks = restored.chunks
            self._audio_files = restored.audio_files
            self._text_dir = task.text_dir
            self._audio_dir = task.audio_dir
            self._last_manifest_write = float('-inf')
            self._retry_sweep_count = 0
            self._retry_in_progress = False

            ready = sum(1 for f in self._audio_files if f is not None)
            logging.info(f"Restored task progress: {ready}/{len(self._chunks)} chunks ready.")
            self._queue_service.update_task_status(TaskStatus.PROCESSING, "Resuming...")
            self._start_generation_process()
            return True
        except Exception as e:
            logging.error(f"Failed to restore task progress from disk: {e}", exc_info=True)
            return False


    def _cleanup_active_resources(self) -> None:
        """Stops timers and resets state."""
        self._progress_timer.stop()
        if self._completion_monitor_timer:
            self._completion_monitor_timer.stop()
        self._terminate_prep_worker()

    def _terminate_prep_worker(self) -> None:
        """Terminates the preparation worker if running."""
        if self._prep_worker and self._prep_worker.isRunning():
            self._prep_worker.requestInterruption()
            if not self._prep_worker.wait(2000):
                logging.warning("Preparation worker did not stop gracefully, forcing termination.")
                self._prep_worker.terminate()
                self._prep_worker.wait()

    def _cleanup_temp_files(self) -> None:
        """Cleans up temporary text and audio directories."""
        try:
            temp_dirs = []
            if self._text_dir:
                temp_dirs.append(self._text_dir)
            if self._audio_dir:
                temp_dirs.append(self._audio_dir)

            if temp_dirs:
                self._file_manager.cleanup_temp_directories(temp_dirs)
        except Exception as e:
            logging.error(f"Error cleaning up: {e}")

    def _cleanup_task_dirs(self, task: GenerationTask) -> None:
        """Removes the temporary text/audio directories recorded on a task."""
        dirs = [d for d in (task.text_dir, task.audio_dir) if d]
        if not dirs:
            return
        try:
            self._file_manager.cleanup_temp_directories(dirs)
        except Exception as e:
            logging.error(f"Error cleaning up temporary files of task {task.id}: {e}")

    def _prepare_output_path(self, task: GenerationTask) -> Path:
        """Prepares the output directory and returns the final MP3 path."""
        base_output_dir = Path(task.config.output_dir_path).expanduser()
        final_dir = base_output_dir / "final"
        self._file_manager.ensure_directory_exists(str(final_dir))

        safe_name = self._sanitize_filename(task.config.project_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return final_dir / f"{safe_name}_{timestamp}.mp3"

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitizes the project name to be a valid filename."""
        cleaned = re.sub(r'[\\/*?:"<>|]', '_', filename)
        cleaned = "".join(c for c in cleaned if c.isprintable())
        return cleaned.strip()

    def _mark_task_completed(self) -> None:
        """Updates current task success state and cleans up."""
        self._queue_service.update_task_progress(100.0)
        self._queue_service.update_task_status(TaskStatus.COMPLETED, "Done")
        self._finalize_task()

    def _handle_assembly_error(self, error: Exception) -> None:
        """Handles errors occurring during background assembly."""
        error_msg = str(error)
        logging.error(f"Assembly failed: {error}", exc_info=True)
        self.errorOccurred.emit(f"Failed to finalize generation: {error_msg}")
        self._handle_task_failure(error_msg)
