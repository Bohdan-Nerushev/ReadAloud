"""
Application controller.

This module orchestrates the entire application, coordinating between GUI, domain, and infrastructure layers.
"""

import logging
import time
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Any, Dict, Set

from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QThread

from src.domain.models import ProjectConfig, AudioChunk, GenerationTask, TaskStatus
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.infrastructure.file_manager import FileManager
from src.application.services.queue_service import QueueService
from src.application.services.generation_service import GenerationService
from src.application.services.assembly_service import AssemblyService


class PreparationWorker(QThread):
    """
    Worker thread to handle file reading, text processing, and chunking
    to avoid blocking the main UI thread.
    """
    finished = pyqtSignal(list, str, str)  # chunks, text_dir, audio_dir
    error = pyqtSignal(str)
    
    def __init__(
            self,
            config: ProjectConfig,
            file_manager: FileManager,
            text_processor: TextProcessor,
            text_chunker: TextChunker
    ) -> None:
        """Initialize the worker."""
        super().__init__()
        self.config = config
        self.file_manager = file_manager
        self.text_processor = text_processor
        self.text_chunker = text_chunker
        
    def run(self) -> None:
        """Execute the preparation tasks."""
        try:
            logging.info(f"Starting preparation for input: {self.config.input_file_path}")
            
            # Read
            with open(self.config.input_file_path, 'r', encoding='utf-8') as f:
                raw_text = f.read()
            
            # Process
            if self.isInterruptionRequested():
                return

            processed_text = self.text_processor.process_text(raw_text)
            
            # Chunk
            if self.isInterruptionRequested():
                return
            
            chunks = self.text_chunker.chunk_text(processed_text)
            
            if not chunks:
                 raise ValueError("No text chunks generated from input file.")

            # Create directories
            if self.isInterruptionRequested():
                return

            workspace_dir = str(Path(self.config.input_file_path).parent)
            text_dir = self.file_manager.create_timestamped_dir("text", workspace_dir)
            audio_dir = self.file_manager.create_timestamped_dir("audio", workspace_dir)
            
            # Save chunks in parallel using ThreadPoolExecutor for faster I/O
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = []
                for chunk in chunks:
                    if self.isInterruptionRequested():
                        break
                    futures.append(executor.submit(self.file_manager.save_text_chunk, chunk, text_dir))
                
                # Wait for all saving operations to complete
                for future in futures:
                    if self.isInterruptionRequested():
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    future.result()

            if self.isInterruptionRequested():
                return
            
            logging.info(f"Preparation complete. Generated {len(chunks)} chunks.")
            self.finished.emit(chunks, text_dir, audio_dir)
            
        except Exception as e:
            logging.error(f"Preparation failed: {e}", exc_info=True)
            self.error.emit(str(e))


class ApplicationController(QObject):
    """
    Central controller that orchestrates audio generation workflow.
    Delegates complex logic to specialized services.
    """

    progressUpdated = pyqtSignal(int, int, str)
    assemblyProgressUpdated = pyqtSignal(float, float)
    generationCompleted = pyqtSignal(str)
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
            assembly_service: AssemblyService
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

        self._prep_worker: Optional[PreparationWorker] = None

        self._text_dir: Optional[str] = None
        self._audio_dir: Optional[str] = None
        self._chunks: List[AudioChunk] = []
        self._audio_files: List[Optional[str]] = []

        self._is_stopped = False

        self._setup_service_subscriptions()
        self._setup_internal_connections()

        self._progress_timer = QTimer()
        self._progress_timer.timeout.connect(self._update_progress_display)
        self._last_signal_time = 0
        self._signal_throttle_ms = 100

        self._completion_monitor_timer: Optional[QTimer] = None

    def _setup_service_subscriptions(self) -> None:
        """Sets up subscriptions to queue service events."""
        self._queue_service.subscribe_task_added(self.taskAdded.emit)
        self._queue_service.subscribe_task_updated(self.taskUpdated.emit)
        self._queue_service.subscribe_status_changed(self.queueStatusChanged.emit)

    def _setup_internal_connections(self) -> None:
        """Sets up connections from generation and assembly services."""
        # Generation Service signals
        self._generation_service.chunkGenerated.connect(self._on_chunk_generated)
        self._generation_service.batchFailed.connect(self._on_batch_failed)
        self._generation_service.errorOccurred.connect(self.errorOccurred.emit)
        
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

    def _get_current_task(self) -> Optional[GenerationTask]:
        """Returns the current task being processed."""
        return self._queue_service.get_current_task()

    def _start_task(self, task: GenerationTask) -> bool:
        """Starts the audio generation process for a specific task."""
        try:
            self._initialize_task_state(task)
            self._start_preparation_worker(task)
            return True
        except Exception as e:
            logging.error(f"Failed to start task: {e}", exc_info=True)
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

        self.progressUpdated.emit(0, 0, "Preparing...")

    def _start_preparation_worker(self, task: GenerationTask) -> None:
        """Starts the preparation worker thread."""
        self._prep_worker = PreparationWorker(
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
            self._audio_files = [None] * len(chunks)

            # Reset assembly service
            self._assembly_service.reset(len(chunks))

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
        """Initializes and starts the generation via GenerationService."""
        task = self._get_current_task()
        if not task:
            return

        self._generation_service.start_generation(
            task, 
            self._chunks, 
            self._audio_dir
        )

        self._progress_timer.start(500)
        self._monitor_completion()

    def _update_progress_display(self) -> None:
        """Updates the progress display via signal."""
        current_time = time.time() * 1000

        if (current_time - self._last_signal_time) < self._signal_throttle_ms:
            return

        self._last_signal_time = current_time
        processed, total, eta = self._generation_service.get_progress_info()
        
        self.progressUpdated.emit(processed, total, eta)

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
        processed, total, eta_str = self._generation_service.get_progress_info()
        
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

    def _on_chunk_generated(self, chunk_number: int, file_path: str) -> None:
        """Callback when a chunk is successfully generated."""
        if self._is_stopped:
            return
            
        # Update audio files list (chunk_number is 1-based)
        if 1 <= chunk_number <= len(self._audio_files):
            self._audio_files[chunk_number - 1] = file_path
            
            # Trigger assembly service to check for batches
            task = self._get_current_task()
            if task and self._audio_dir:
                self._assembly_service.check_and_submit_batches(
                    self._audio_files,
                    self._audio_dir,
                    task.config.speed
                )

    def _on_batch_failed(self, batch: List[AudioChunk], error_msg: str) -> None:
        """Callback when batch generation fails."""
        logging.warning(f"Batch generation failed: {error_msg}")
        # GenerationService handles retry, so this is likely a final failure for this batch.
        # We should probably error out the task if chunks fail permanently.
        # But for now, just log. 
        pass

    def _monitor_completion(self) -> None:
        """Monitors generation completion and triggers final assembly."""
        def check_completion() -> None:
            if self._is_stopped:
                return

            processed, total, _ = self._generation_service.get_progress_info()
            if total > 0 and processed >= total:
                if self._completion_monitor_timer:
                    self._completion_monitor_timer.stop()
                self._progress_timer.stop()
                self._finalize_generation()

        self._completion_monitor_timer = QTimer()
        self._completion_monitor_timer.timeout.connect(check_completion)
        self._completion_monitor_timer.start(1000)

    def _finalize_generation(self) -> None:
        """Finalizes generation by assembling audio and cleaning up."""
        task = self._get_current_task()
        if not task:
            return

        logging.info("Finalizing generation...")
        try:
            output_path = self._prepare_output_path(task)
            
            # Start assembly in a background thread to not block UI
            thread = threading.Thread(
                target=self._execute_assembly_workflow,
                args=(output_path, task.config.speed),
                daemon=True
            )
            thread.start()
            
        except Exception as e:
            logging.error(f"Failed to prepare final generation: {e}")
            self.errorOccurred.emit(f"Failed to prepare final generation: {str(e)}")

    def _execute_assembly_workflow(self, output_path: Path, speed: float) -> None:
        """Core assembly workflow executed in a separate thread."""
        try:
            self._assembly_service.assemble_final(
                output_path, 
                self._audio_files, 
                speed
            )
            self._cleanup_temp_files()
            self._mark_task_completed()
        except Exception as e:
            self._handle_assembly_error(e)

    def _on_assembly_progress(self, percentage: float, remaining: float) -> None:
        """Callback for assembly progress signals."""
        # Convert 0-100% to 90-100%
        final_percentage = 90.0 + (percentage * 0.1)
        self._queue_service.update_task_progress(final_percentage)
        self.assemblyProgressUpdated.emit(percentage, remaining)

    def _on_assembly_error(self, error_msg: str) -> None:
        logging.error(f"Assembly error: {error_msg}")

    def pause_generation(self) -> None:
        """Pauses the ongoing audio generation."""
        is_paused = self._generation_service.pause()
        if is_paused:
            self._queue_service.update_task_status(TaskStatus.PAUSED, "Paused")
            logging.info("Generation paused")
        else:
            self._queue_service.update_task_status(TaskStatus.PROCESSING, "Resumed")
            logging.info("Generation resumed")

    def stop_generation(self) -> None:
        """Stops the audio generation and cleans up."""
        self._is_stopped = True
        logging.info("Stopping generation...")

        self._queue_service.update_task_status(TaskStatus.STOPPED, "Stopped by user")

        self. _terminate_prep_worker()
        self._generation_service.stop()
        self._assembly_service.stop()
        
        self._cleanup_temp_files()
        self._finalize_task()

    def cancel_task(self, task_id: str) -> None:
        """Cancels a specific task by its ID."""
        current_task = self._get_current_task()
        if current_task and str(current_task.id) == task_id:
            self.stop_generation()
        else:
            logging.info(f"Cancelling queued task: {task_id}")
            self._queue_service.remove_task(task_id)
            self._emit_global_progress()

    def _handle_task_failure(self, error_msg: str) -> None:
        """Handles task failure and moves to next."""
        self._queue_service.update_task_status(TaskStatus.FAILED, error_msg)
        self.errorOccurred.emit(error_msg)
        self._finalize_task()

    def _finalize_task(self) -> None:
        """Clean up current task and process next."""
        self._queue_service.finalize_current_task()
        self._cleanup_active_resources()
        self._process_queue()
        self._emit_global_progress()

    def _cleanup_active_resources(self) -> None:
        """Stops timers and resets state."""
        self._progress_timer.stop()
        if self._completion_monitor_timer:
            self._completion_monitor_timer.stop()

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

    def _prepare_output_path(self, task: GenerationTask) -> Path:
        """Prepares the output directory and returns the final MP3 path."""
        base_output_dir = Path(task.config.output_dir_path)
        final_dir = base_output_dir / "final"
        self._file_manager.ensure_directory_exists(str(final_dir))

        safe_name = self._sanitize_filename(task.config.project_name)
        return final_dir / f"{safe_name}.mp3"

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
