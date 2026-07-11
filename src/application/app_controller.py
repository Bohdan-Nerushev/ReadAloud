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
import os
from src.infrastructure.file_manager import FileManager
from src.application.services.queue_service import QueueService
from src.application.services.generation_service import GenerationService
from src.application.services.assembly_service import AssemblyService
from src.application.services.persistence_service import PersistenceService
from src.infrastructure.logging_config import set_correlation_id




class PreparationWorker(QThread):
    """
    Worker thread to handle file reading, text processing, and chunking
    to avoid blocking the main UI thread.
    """
    finished = pyqtSignal(list, str, str)  # chunks, text_dir, audio_dir
    error = pyqtSignal(str)
    
    def __init__(
            self,
            task_id: str,
            config: ProjectConfig,
            file_manager: FileManager,
            text_processor: TextProcessor,
            text_chunker: TextChunker
    ) -> None:
        """Initialize the worker."""
        super().__init__()
        self.task_id = task_id
        self.config = config
        self.file_manager = file_manager
        self.text_processor = text_processor
        self.text_chunker = text_chunker

        
    def run(self) -> None:
        """Execute the preparation tasks."""
        # Set correlation ID for this worker thread
        set_correlation_id(self.task_id)

        
        try:
            logging.info(f"Starting preparation for input: {self.config.input_file_path}")

            
            # Read, Process, and Chunk incrementally to avoid OOM
            chunks = []
            chunk_num = 1
            remaining = ""
            buffer_size = 10 * 1024 * 1024  # 10MB

            with open(self.config.input_file_path, 'r', encoding='utf-8') as f:
                while True:
                    if self.isInterruptionRequested():
                        return

                    block = f.read(buffer_size)
                    if not block:
                        break

                    text = remaining + block
                    # Ensure we don't split words across buffers
                    if len(block) == buffer_size:
                        last_space = text.rfind(' ')
                        if last_space != -1:
                            to_chunk = text[:last_space]
                            remaining = text[last_space:]
                        else:
                            # Fallback: process entire block if no space found
                            to_chunk = text
                            remaining = ""
                    else:
                        to_chunk = text
                        remaining = ""

                    if to_chunk.strip():
                        processed = self.text_processor.process_text(to_chunk)
                        if processed.strip():
                            block_chunks = self.text_chunker.chunk_text(processed)
                            for c in block_chunks:
                                # Create new chunk because AudioChunk is frozen
                                chunks.append(AudioChunk(
                                    chunk_number=chunk_num,
                                    text_content=c.text_content
                                ))
                                chunk_num += 1

            if remaining.strip():
                processed = self.text_processor.process_text(remaining)
                if processed.strip():
                    block_chunks = self.text_chunker.chunk_text(processed)
                    for c in block_chunks:
                        chunks.append(AudioChunk(
                            chunk_number=chunk_num,
                            text_content=c.text_content
                        ))
                        chunk_num += 1
            
            if not chunks:
                 raise ValueError("No text chunks generated from input file.")

            # Create directories
            if self.isInterruptionRequested():
                return

            workspace_dir = str(Path(self.config.input_file_path).expanduser().parent)
            text_dir = self.file_manager.create_timestamped_dir("text", workspace_dir)
            audio_dir = self.file_manager.create_timestamped_dir("audio", workspace_dir)
            
            # Save chunks in parallel using ThreadPoolExecutor for faster I/O
            executor = ThreadPoolExecutor(max_workers=4)
            interrupted = False
            try:
                futures = []
                for chunk in chunks:
                    if self.isInterruptionRequested():
                        interrupted = True
                        break
                    futures.append(executor.submit(self.file_manager.save_text_chunk, chunk, text_dir))
                
                if not interrupted:
                    # Wait for all saving operations to complete
                    for future in futures:
                        if self.isInterruptionRequested():
                            interrupted = True
                            break
                        future.result()
            finally:
                if interrupted:
                    executor.shutdown(wait=False, cancel_futures=True)
                else:
                    executor.shutdown(wait=True)

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

    progressUpdated = pyqtSignal(int, int, str, float) # processed, total, eta_str, chunks_per_sec
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
            assembly_service: AssemblyService,
            persistence_service: PersistenceService
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
        self._queue_service.subscribe_task_added(lambda task: self._save_state())
        self._queue_service.subscribe_task_updated(self.taskUpdated.emit)
        self._queue_service.subscribe_task_updated(lambda task: self._save_state())
        self._queue_service.subscribe_status_changed(self.queueStatusChanged.emit)


    def _setup_internal_connections(self) -> None:
        """Sets up connections from generation and assembly services."""
        # Generation Service signals
        self._generation_service.chunkGenerated.connect(self._on_chunk_generated)
        self._generation_service.batchGenerated.connect(self._on_batch_generated)
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
            set_correlation_id(str(task.id))
            
            logging.info(f"Starting task: {task.id} ({task.config.project_name})")

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
            self._audio_files = [None] * len(chunks)

            # Reset assembly service
            self._assembly_service.reset(len(chunks))

            task = self._get_current_task()
            if task:
                task.text_dir = text_dir
                task.audio_dir = audio_dir
                self._save_state()

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

    def _on_batch_generated(self, results: List[tuple]) -> None:
        """Callback for batch generation results to ensure no chunks are missed."""
        # Individual on_chunk_generated handles most logic, but we can double check here
        # or use this to further reduce signals if we wanted to.
        pass

    def _on_batch_failed(self, batch: List[AudioChunk], error_msg: str) -> None:
        """Callback when batch generation fails definitively."""
        logging.error(f"Batch generation failed definitively: {error_msg}")
        self._handle_task_failure(f"Generation failed: {error_msg}")

    def _monitor_completion(self) -> None:
        """Monitors generation completion and triggers final assembly."""
        def check_completion() -> None:
            if self._is_stopped:
                return

            processed, total, _, _ = self._generation_service.get_progress_info()
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
        """Core assembly workflow executed in a separate thread."""
        try:
            self._assembly_service.assemble_final(
                output_path, 
                self._audio_files, 
                speed,
                correlation_id
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

    def pause_generation(self, task_id: Optional[str] = None) -> None:
        """Pauses the ongoing audio generation."""
        current_task = self._get_current_task()
        if task_id is None and current_task:
            task_id = str(current_task.id)

        if not task_id:
            return

        if current_task and str(current_task.id) == task_id:
            if current_task.status == TaskStatus.PAUSED:
                if not self._generation_service._thread_manager:
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
                self._save_state()
                if updated_task.status == TaskStatus.PENDING:
                    self._process_queue()

    def shutdown(self) -> None:
        """Gracefully stops ongoing processing and saves queue state for next run."""
        logging.info("Shutting down ApplicationController...")
        self._is_stopped = True
        self._terminate_prep_worker()
        self._cleanup_active_resources()
        self._generation_service.stop()
        self._assembly_service.stop()
        
        current_task = self._get_current_task()
        if current_task and current_task.status in [TaskStatus.PROCESSING, TaskStatus.PAUSED]:
            current_task.status = TaskStatus.PAUSED
            current_task.message = "Paused"
            
        self._save_state()

    def stop_generation(self) -> None:
        """Stops the audio generation for all tasks and cleans up."""
        self._is_stopped = True
        logging.info("Stopping all generation tasks...")

        self._terminate_prep_worker()
        self._generation_service.stop()
        self._assembly_service.stop()
        
        self._queue_service.clear_all_tasks()
        self._cleanup_temp_files()
        self._save_state()
        self._finalize_task()

    def cancel_task(self, task_id: str) -> None:
        """Cancels a specific task by its ID."""
        current_task = self._get_current_task()
        if current_task and str(current_task.id) == task_id:
            self.stop_generation()
        else:
            logging.info(f"Cancelling queued task: {task_id}")
            self._queue_service.remove_task(task_id)
            self._save_state()
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
        self._save_state()
        self._process_queue()
        self._emit_global_progress()

    def _save_state(self) -> None:
        """Saves current queue state to persistence."""
        try:
            tasks = self._queue_service.get_all_tasks()
            self._persistence_service.save_state(tasks)
        except Exception as e:
            logging.error(f"Failed to auto-save state: {e}", exc_info=True)

    def restore_state(self) -> None:
        """Restores queue state from persistence."""
        try:
            tasks = self._persistence_service.load_state()
            if not tasks:
                return

            logging.info(f"Restoring {len(tasks)} tasks from saved state.")
            
            first_task = tasks[0]
            if first_task.status in [TaskStatus.PROCESSING, TaskStatus.PAUSED]:
                first_task.status = TaskStatus.PAUSED
                first_task.message = "Paused"
                self._queue_service._current_task = first_task
                self._queue_service.statusChanged.emit(True)
                self.taskAdded.emit(first_task)
                self.taskUpdated.emit(first_task)
                
                for task in tasks[1:]:
                    task.status = TaskStatus.PENDING
                    task.message = "Waiting..."
                    self._queue_service._task_queue.append(task)
                    self.taskAdded.emit(task)
            else:
                for task in tasks:
                    task.status = TaskStatus.PENDING
                    task.message = "Waiting..."
                    self._queue_service._task_queue.append(task)
                    self.taskAdded.emit(task)
            
            self._emit_global_progress()
        except Exception as e:
            logging.error(f"Error restoring state: {e}", exc_info=True)

    def _restore_progress_from_disk(self, task: GenerationTask) -> bool:
        """Attempts to restore task chunks and progress from physical files on disk."""
        try:
            text_path = Path(task.text_dir)
            audio_path = Path(task.audio_dir)
            
            txt_files = list(text_path.glob("*.txt"))
            if not txt_files:
                return False
                
            chunks_data = []
            for f in txt_files:
                try:
                    num = int(f.stem)
                    chunks_data.append((num, f))
                except ValueError:
                    continue
            
            if not chunks_data:
                return False
                
            chunks_data.sort(key=lambda x: x[0])
            
            chunks = []
            for num, f in chunks_data:
                try:
                    with open(f, "r", encoding="utf-8") as file:
                        content = file.read()
                    chunks.append(AudioChunk(
                        chunk_number=num,
                        text_content=content,
                        text_file_path=str(f.absolute())
                    ))
                except Exception as e:
                    logging.error(f"Failed to read restored chunk file {f}: {e}")
                    return False
            
            self._chunks = chunks
            self._text_dir = task.text_dir
            self._audio_dir = task.audio_dir
            self._audio_files = [None] * len(chunks)
            
            self._assembly_service.reset(len(chunks))
            
            for idx, c in enumerate(self._chunks):
                chunk_audio = audio_path / f"{c.chunk_number}.mp3"
                if chunk_audio.exists() and chunk_audio.stat().st_size > 0:
                    duration = self._generation_service._audio_generator._get_file_duration_fast(str(chunk_audio))
                    self._audio_files[idx] = str(chunk_audio.absolute())
                    self._chunks[idx] = c.with_audio_path(str(chunk_audio.absolute()), duration)
                    
                    batch_index = self._assembly_service.mark_chunk_ready(idx, duration)
                    if batch_index is not None:
                        part_path = audio_path / f"part_{batch_index}.mp3"
                        if part_path.exists() and part_path.stat().st_size > 0:
                            with self._assembly_service._state_lock:
                                self._assembly_service._batch_results[batch_index] = str(part_path)
                                self._assembly_service._batch_submitted.add(batch_index)
                            logging.info(f"Restoring assembled batch part {batch_index} from disk.")
                        else:
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
            
            logging.info(f"Restored task progress: {sum(1 for f in self._audio_files if f is not None)}/{len(chunks)} chunks ready.")
            
            self._queue_service.update_task_status(
                TaskStatus.PROCESSING,
                "Resuming..."
            )
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
        base_output_dir = Path(task.config.output_dir_path).expanduser()
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
