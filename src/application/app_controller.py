"""
Application controller.

This module orchestrates the entire application, coordinating between GUI, domain, and infrastructure layers.
"""

import logging
import threading
import re
import time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Any, Dict, Set
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QThread
from src.domain.models import ProjectConfig, AudioChunk, GenerationTask, TaskStatus
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.domain.exceptions import ReadAloudException
from src.infrastructure.file_manager import FileManager
from src.infrastructure.progress_tracker import ProgressTracker
from src.infrastructure.thread_manager import ThreadManager
from src.infrastructure.retry_handler import RetryHandler
from src.application.services.queue_service import QueueService


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
            processed_text = self.text_processor.process_text(raw_text)
            
            # Chunk
            chunks = self.text_chunker.chunk_text(processed_text)
            
            if not chunks:
                 raise ValueError("No text chunks generated from input file.")

            # Create directories
            workspace_dir = str(Path(self.config.input_file_path).parent)
            text_dir = self.file_manager.create_timestamped_dir("text", workspace_dir)
            audio_dir = self.file_manager.create_timestamped_dir("audio", workspace_dir)
            
            # Save chunks
            for chunk in chunks:
                self.file_manager.save_text_chunk(chunk, text_dir)
            
            logging.info(f"Preparation complete. Generated {len(chunks)} chunks.")
            self.finished.emit(chunks, text_dir, audio_dir)
            
        except Exception as e:
            logging.error(f"Preparation failed: {e}", exc_info=True)
            self.error.emit(str(e))


class ApplicationController(QObject):
    """
    Central controller that orchestrates audio generation workflow.
    """

    progressUpdated = pyqtSignal(int, int, str)
    assemblyProgressUpdated = pyqtSignal(float, float)
    generationCompleted = pyqtSignal(str)
    errorOccurred = pyqtSignal(str)

    taskAdded = pyqtSignal(GenerationTask)
    taskUpdated = pyqtSignal(GenerationTask)
    queueStatusChanged = pyqtSignal(bool)

    def __init__(
            self,
            queue_service: QueueService,
            text_processor: TextProcessor,
            text_chunker: TextChunker,
            audio_generator: AudioGenerator,
            audio_assembler: AudioAssembler,
            file_manager: FileManager,
            retry_handler: RetryHandler
    ) -> None:
        """Initialize the ApplicationController."""
        super().__init__()

        self._queue_service = queue_service
        self._text_processor = text_processor
        self._text_chunker = text_chunker
        self._audio_generator = audio_generator
        self._audio_assembler = audio_assembler
        self._file_manager = file_manager
        self._retry_handler = retry_handler

        self._thread_manager: Optional[ThreadManager] = None
        self._progress_tracker: Optional[ProgressTracker] = None
        self._prep_worker: Optional[PreparationWorker] = None

        self._text_dir: Optional[str] = None
        self._audio_dir: Optional[str] = None
        self._chunks: List[AudioChunk] = []
        self._audio_files: List[Any] = []

        self._is_stopped = False

        self._progress_timer = QTimer()
        self._progress_timer.timeout.connect(self._update_progress_display)
        self._completion_timer: Optional[QTimer] = None

        self.BATCH_SIZE = 50
        self._batch_submitted: Set[int] = set()
        self._batch_results: Dict[int, str] = {}
        self._assembly_executor: Optional[ThreadPoolExecutor] = None

        self._last_signal_time = 0
        self._signal_throttle_ms = 100

        self._setup_service_subscriptions()

    def _setup_service_subscriptions(
            self
    ) -> None:
        """Sets up subscriptions to service events."""
        self._queue_service.subscribe_task_added(self.taskAdded.emit)
        self._queue_service.subscribe_task_updated(self.taskUpdated.emit)
        self._queue_service.subscribe_status_changed(self.queueStatusChanged.emit)

    def add_task(
            self,
            config: ProjectConfig
    ) -> None:
        """
        Adds a new task to the queue.

        Args:
            config: Project configuration for the task
        """
        self._queue_service.add_task(config)
        self._process_queue()

    def _process_queue(
            self
    ) -> None:
        """Processes the next task in the queue if idle."""
        task = self._queue_service.get_next_task()
        if task:
            self._start_task(task)

    def _get_current_task(
            self
    ) -> Optional[GenerationTask]:
        """Returns the current task being processed."""
        return self._queue_service.get_current_task()

    def _start_task(
            self,
            task: GenerationTask
    ) -> bool:
        """Starts the audio generation process for a specific task."""
        try:
            self._initialize_task_state(task)
            self._start_preparation_worker(task)
            return True
        except Exception as e:
            logging.error(f"Failed to start task: {e}", exc_info=True)
            self._handle_task_failure(f"Failed to start: {str(e)}")
            return False

    def _initialize_task_state(
            self,
            task: GenerationTask
    ) -> None:
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

        self._batch_submitted = set()
        self._batch_results = {}
        self._assembly_executor = ThreadPoolExecutor(max_workers=2)

        self.progressUpdated.emit(0, 0, "Preparing...")

    def _start_preparation_worker(
            self,
            task: GenerationTask
    ) -> None:
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

    def _handle_task_failure(
            self,
            error_msg: str
    ) -> None:
        """Handles task failure and moves to next."""
        self._queue_service.update_task_status(
            TaskStatus.FAILED,
            error_msg
        )
        self.errorOccurred.emit(error_msg)
        self._finalize_task()

    def _finalize_task(
            self
    ) -> None:
        """Clean up current task and process next."""
        self._queue_service.finalize_current_task()
        self._cleanup_active_resources()
        self._process_queue()

    def _cleanup_active_resources(
            self
    ) -> None:
        """Stops active threads and timers."""
        if self._thread_manager:
            self._thread_manager.stop()

        self._progress_timer.stop()

        if self._completion_timer:
            self._completion_timer.stop()

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

            self._start_generation_process()
        except Exception as e:
            logging.error(f"Error after preparation: {e}", exc_info=True)
            self.errorOccurred.emit(f"System error after preparation: {e}")

    def _start_generation_process(
            self
    ) -> None:
        """Initializes and starts the generation trackers and threads."""
        task = self._get_current_task()
        if not task:
            return

        self._progress_tracker = ProgressTracker(
            total_chunks=len(self._chunks)
        )
        self._progress_tracker.start()

        self._thread_manager = ThreadManager(
            thread_count=task.config.thread_count
        )
        self._thread_manager.start()

        self._submit_chunks_to_threads(task)

        self._progress_timer.start(500)
        self._monitor_completion()

    def _submit_chunks_to_threads(
            self,
            task: GenerationTask
    ) -> None:
        """Submits all chunks to the thread manager."""
        for chunk in self._chunks:
            self._thread_manager.submit_task(
                self._generate_chunk_audio,
                chunk,
                task.config.language,
                task.config.gender
            )

    def _on_preparation_error(self, error_msg: str) -> None:
        """Called when preparation worker fails."""
        logging.error(f"Preparation worker error: {error_msg}")
        self.errorOccurred.emit(f"Preparation failed: {error_msg}")

    def pause_generation(
            self
    ) -> None:
        """Pauses the ongoing audio generation."""
        if self._thread_manager:
            if self._thread_manager.is_paused():
                self._thread_manager.resume()
                logging.info("Generation resumed")
            else:
                self._thread_manager.pause()
                logging.info("Generation paused")
    
    def stop_generation(
            self
    ) -> None:
        """Stops the audio generation and cleans up temporary files."""
        self._is_stopped = True
        logging.info("Stopping generation...")

        self._queue_service.update_task_status(
            TaskStatus.STOPPED,
            "Stopped by user"
        )

        self._terminate_prep_worker()
        self._cleanup_temp_files()
        self._finalize_task()
    
    def cancel_task(
            self,
            task_id: str
    ) -> None:
        """
        Cancels a specific task by its ID.
        
        Args:
            task_id: UUID of the task to cancel
        """
        current_task = self._get_current_task()
        
        if current_task and str(current_task.id) == task_id:
            # Cancel currently running task
            self.stop_generation()
        else:
            # Task is in queue, remove it
            logging.info(f"Cancelling queued task: {task_id}")

    def _terminate_prep_worker(
            self
    ) -> None:
        """Terminates the preparation worker if running."""
        if self._prep_worker and self._prep_worker.isRunning():
            self._prep_worker.terminate()
            self._prep_worker.wait()

    def _cleanup_temp_files(
            self
    ) -> None:
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
    
    def _generate_chunk_audio(
            self,
            chunk: AudioChunk,
            language: str,
            gender: str
    ) -> None:
        """Generates audio for a single chunk with retry logic."""
        if self._is_stopped:
            return

        try:
            audio_path = self._retry_handler.execute_with_retry(
                self._audio_generator.generate_audio,
                chunk,
                language,
                gender,
                self._audio_dir,
                max_retries=5,
                backoff=2.0
            )

            self._audio_files[chunk.chunk_number - 1] = audio_path
            self._progress_tracker.update_progress(success=True)

        except Exception as e:
            logging.error(f"Failed chunk {chunk.chunk_number}: {e}")
            self._progress_tracker.update_progress(success=False)
    
    def _update_progress_display(
            self
    ) -> None:
        """Updates the progress display via signal."""
        current_time = time.time() * 1000

        if (current_time - self._last_signal_time) < self._signal_throttle_ms:
            return

        if self._progress_tracker:
            self._emit_progress_signals(current_time)

    def _emit_progress_signals(
            self,
            current_time: float
    ) -> None:
        """Emits progress signals for UI and task updates."""
        self._last_signal_time = current_time
        total = self._progress_tracker.get_total_count()
        eta = self._progress_tracker.get_eta_string()

        self.progressUpdated.emit(
            self._progress_tracker.get_processed_count(),
            total,
            eta
        )

        # Limit generation phase to 90% maximum
        raw_percentage = self._progress_tracker.get_progress_percentage()
        capped_percentage = min(raw_percentage, 90.0)
        self._queue_service.update_task_progress(capped_percentage)
    
    def _monitor_completion(
            self
    ) -> None:
        """
        Monitors generation completion and triggers final assembly.
        """
        def check_completion() -> None:
            if self._progress_tracker and not self._is_stopped:
                processed = self._progress_tracker.get_processed_count()
                total = self._progress_tracker.get_total_count()
                
                if processed >= total:
                    if self._completion_timer:
                        self._completion_timer.stop()
                    self._progress_timer.stop()
                    self._finalize_generation()
                else:
                    self._check_batch_assembly()
        
        completion_timer = QTimer()
        completion_timer.timeout.connect(check_completion)
        completion_timer.start(1000)
        
        self._completion_timer = completion_timer
    
    def _sanitize_filename(
            self,
            filename: str
    ) -> str:
        r"""
        Sanitizes the project name to be a valid filename.
        Removes/replaces special characters: \ / : * ? " < > |
        """
        # Replace forbidden characters with underscore
        cleaned = re.sub(r'[\\/*?:"<>|]', '_', filename)
        # Remove control characters
        cleaned = "".join(c for c in cleaned if c.isprintable())
        return cleaned.strip()

    def _finalize_generation(
            self
    ) -> None:
        """Finalizes generation by assembling audio and cleaning up."""
        task = self._get_current_task()
        if not task:
            return

        logging.info("Finalizing generation...")
        try:
            output_path = self._prepare_output_path(task)
            self._start_assembly_thread(output_path)
        except Exception as e:
            logging.error(f"Failed to prepare final generation: {e}")
            self.errorOccurred.emit(f"Failed to prepare final generation: {str(e)}")

    def _prepare_output_path(
            self,
            task: GenerationTask
    ) -> Path:
        """Prepares the output directory and returns the final MP3 path."""
        base_output_dir = Path(task.config.output_dir_path)
        final_dir = base_output_dir / "final"
        self._file_manager.ensure_directory_exists(str(final_dir))

        safe_name = self._sanitize_filename(task.config.project_name)
        return final_dir / f"{safe_name}.mp3"

    def _start_assembly_thread(
            self,
            output_path: Path
    ) -> None:
        """Starts the background assembly thread."""
        thread = threading.Thread(
            target=self._execute_assembly_workflow,
            args=(output_path,),
            daemon=True
        )
        thread.start()

    def _execute_assembly_workflow(
            self,
            output_path: Path
    ) -> None:
        """Core assembly workflow executed in a separate thread."""
        try:
            self._check_batch_assembly()
            self._wait_for_batches()
            self._perform_final_assembly(output_path)
            self._cleanup_temp_files()
            self._mark_task_completed()
        except Exception as e:
            self._handle_assembly_error(e)

    def _wait_for_batches(
            self
    ) -> None:
        """Waits for all background batch assembly tasks to complete."""
        logging.info("Waiting for background assembly tasks...")
        if self._assembly_executor:
            self._assembly_executor.shutdown(wait=True)

    def _perform_final_assembly(
            self,
            output_path: Path
    ) -> None:
        """Decides on assembly strategy and performs final merge."""
        task = self._get_current_task()
        valid_files = [f for f in self._audio_files if f is not None]

        if not valid_files:
            raise ReadAloudException("No audio files were generated successfully")

        parts, use_fast_mode = self._get_assembly_parts()

        if use_fast_mode:
            self._assemble_fast(parts, output_path)
        else:
            self._assemble_full(valid_files, output_path, task.config.speed)
        
        # Verify that the final file was created successfully
        if not output_path.exists():
            raise ReadAloudException("Final audio file was not created")
        
        if output_path.stat().st_size == 0:
            raise ReadAloudException("Final audio file is empty")

    def _get_assembly_parts(
            self
    ) -> tuple[List[str], bool]:
        """Determines if fast assembly via pre-processed batches is possible."""
        total_batches = (len(self._chunks) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        ordered_parts = []

        for i in range(total_batches):
            if i not in self._batch_results:
                return [], False
            ordered_parts.append(self._batch_results[i])

        return ordered_parts, True

    def _assemble_fast(
            self,
            parts: List[str],
            output_path: Path
    ) -> None:
        """Assembles pre-processed parts using fast copy codec."""
        logging.info(f"Assembling {len(parts)} parts (Fast mode)")
        self._audio_assembler.assemble_audio(
            parts,
            str(output_path),
            speed=1.0,
            callback=self._assembly_progress_callback,
            copy_codec=True
        )

    def _assemble_full(
            self,
            files: List[str],
            output_path: Path,
            speed: float
    ) -> None:
        """Assembles raw chunks using full processing."""
        logging.info(f"Assembling {len(files)} chunks (Fallback mode)")
        self._audio_assembler.assemble_audio(
            files,
            str(output_path),
            speed=speed,
            callback=self._assembly_progress_callback,
            copy_codec=False
        )

    def _assembly_progress_callback(
            self,
            percentage: float,
            remaining: float
    ) -> None:
        """Callback for assembly progress signals."""
        # Convert assembly progress from 0-100% to 90-100% range
        final_percentage = 90.0 + (percentage * 0.1)
        self._update_assembly_task_progress(final_percentage)
        self.assemblyProgressUpdated.emit(percentage, remaining)
    
    def _update_assembly_task_progress(
            self,
            percentage: float
    ) -> None:
        """Updates task progress during assembly phase (90-100%)."""
        self._queue_service.update_task_progress(percentage)

    def _mark_task_completed(
            self
    ) -> None:
        """Updates current task success state and cleans up."""
        # Set to 100% only after final assembly is complete
        self._queue_service.update_task_progress(100.0)
        self._queue_service.update_task_status(
            TaskStatus.COMPLETED,
            "Done"
        )

        self._finalize_task()

    def _handle_assembly_error(
            self,
            error: Exception
    ) -> None:
        """Handles errors occurring during background assembly."""
        error_msg = str(error)
        logging.error(f"Assembly failed: {error}", exc_info=True)
        self.errorOccurred.emit(f"Failed to finalize generation: {error_msg}")
        self._handle_task_failure(error_msg)
    
    def _check_batch_assembly(self) -> None:
        """Checks if enough chunks are ready for a batch merge."""
        if not self._chunks:
            return

        total_batches = (len(self._chunks) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        
        for i in range(total_batches):
            if i in self._batch_submitted:
                continue
            
            start_idx = i * self.BATCH_SIZE
            end_idx = min(start_idx + self.BATCH_SIZE, len(self._chunks))
            
            # Check if all files in this range are generated
            if all(self._audio_files[k] is not None for k in range(start_idx, end_idx)):
                self._trigger_batch_assembly(i, start_idx, end_idx)

    def _trigger_batch_assembly(
            self,
            batch_index: int,
            start_idx: int,
            end_idx: int
    ) -> None:
        """Triggers a background assembly task for a batch."""
        task = self._get_current_task()
        if not task:
            return

        self._batch_submitted.add(batch_index)

        chunk_files = self._audio_files[start_idx:end_idx]
        output_dir = Path(self._audio_dir)
        part_path = output_dir / f"part_{batch_index}.mp3"

        def _assemble_task():
            try:
                self._audio_assembler.assemble_audio(
                    chunk_files,
                    str(part_path),
                    speed=task.config.speed,
                    copy_codec=False
                )
                return str(part_path)
            except Exception as e:
                logging.error(f"Batch {batch_index} assembly failed: {e}")
                raise

        future = self._assembly_executor.submit(_assemble_task)

        def _done_callback(fut):
            try:
                result_path = fut.result()
                self._batch_results[batch_index] = result_path
            except Exception as e:
                logging.error(f"Batch {batch_index} callback error: {e}")

        future.add_done_callback(_done_callback)

    def is_paused(
            self
    ) -> bool:
        """
        Checks if generation is currently paused.
        
        Returns:
            True if paused, False otherwise
        """
        if self._thread_manager:
            return self._thread_manager.is_paused()
        return False
