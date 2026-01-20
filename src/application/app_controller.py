"""
Application controller.

This module orchestrates the entire application, coordinating between GUI, domain, and infrastructure layers.
"""

import logging
import threading
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Any, Dict, Set
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QThread
from src.domain.models import ProjectConfig, AudioChunk
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.infrastructure.file_manager import FileManager
from src.infrastructure.progress_tracker import ProgressTracker
from src.infrastructure.thread_manager import ThreadManager, ThreadState
from src.infrastructure.retry_handler import RetryHandler


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
    
    Manages application state, coordinates services, and communicates with GUI via signals.
    """
    
    progressUpdated = pyqtSignal(int, int, str)
    assemblyProgressUpdated = pyqtSignal(float, float)  # percentage, remaining seconds
    generationCompleted = pyqtSignal(str)
    errorOccurred = pyqtSignal(str)
    
    def __init__(
            self
    ) -> None:
        """Initialize the ApplicationController."""
        super().__init__()
        
        self._text_processor = TextProcessor()
        self._text_chunker = TextChunker()
        self._audio_generator = AudioGenerator()
        self._audio_assembler = AudioAssembler()
        self._file_manager = FileManager()
        self._retry_handler = RetryHandler()
        
        self._thread_manager: Optional[ThreadManager] = None
        self._progress_tracker: Optional[ProgressTracker] = None
        self._prep_worker: Optional[PreparationWorker] = None
        
        self._text_dir: Optional[str] = None
        self._audio_dir: Optional[str] = None
        self._chunks: List[AudioChunk] = []
        self._audio_files: List[Any] = [] # List[Optional[str]]
        
        self._is_stopped = False
        self.config: Optional[ProjectConfig] = None
        
        self._progress_timer = QTimer()
        self._progress_timer.timeout.connect(self._update_progress_display)
        self._completion_timer: Optional[QTimer] = None
        
        # Batch assembly
        self.BATCH_SIZE = 50
        self._batch_submitted: Set[int] = set()
        self._batch_results: Dict[int, str] = {}
        self._assembly_executor: Optional[ThreadPoolExecutor] = None
        
        self._last_signal_time = 0
        self._signal_throttle_ms = 100
    
    def start_generation(
            self,
            config: ProjectConfig
    ) -> bool:
        """
        Starts the audio generation process.
        
        Args:
            config: Project configuration containing all necessary parameters
            
        Returns:
            True if started successfully (moved to background), False otherwise
        """
        try:
            self._is_stopped = False
            self.config = config
            
            # Reset state
            self._chunks = []
            self._audio_files = []
            self._text_dir = None
            self._audio_dir = None
            
            # Reset batch assembly
            self._batch_submitted = set()
            self._batch_results = {}
            # Max 2 concurrent assembly tasks (ffmpeg is heavy)
            self._assembly_executor = ThreadPoolExecutor(max_workers=2)
            
            # Show "Preparing..." state
            # We use 0, 0, "Preparing..." to indicate indefinite progress
            self.progressUpdated.emit(0, 0, "Preparing...")
            
            self._prep_worker = PreparationWorker(
                config,
                self._file_manager,
                self._text_processor,
                self._text_chunker
            )
            self._prep_worker.finished.connect(self._on_preparation_finished)
            self._prep_worker.error.connect(self._on_preparation_error)
            self._prep_worker.start()
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to start generation: {e}", exc_info=True)
            self.errorOccurred.emit(f"Failed to start generation: {str(e)}")
            return False

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
            
            logging.info(f"Starting processing of {len(chunks)} chunks using {self.config.thread_count} threads.")
            
            self._progress_tracker = ProgressTracker(
                total_chunks=len(self._chunks)
            )
            self._progress_tracker.start()
            
            self._thread_manager = ThreadManager(
                thread_count=self.config.thread_count
            )
            self._thread_manager.start()
            
            for chunk in self._chunks:
                self._thread_manager.submit_task(
                    self._generate_chunk_audio,
                    chunk,
                    self.config.language,
                    self.config.gender
                )
            
            self._progress_timer.start(500)
            self._monitor_completion()
            
        except Exception as e:
            logging.error(f"Error after preparation: {e}", exc_info=True)
            self.errorOccurred.emit(f"System error after preparation: {e}")

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
        
        if self._prep_worker and self._prep_worker.isRunning():
            self._prep_worker.terminate()
            self._prep_worker.wait()
        
        if self._thread_manager:
            self._thread_manager.stop()
        
        self._progress_timer.stop()
        if self._completion_timer:
            self._completion_timer.stop()
        
        try:
            temp_dirs = []
            if self._text_dir:
                temp_dirs.append(self._text_dir)
            if self._audio_dir:
                temp_dirs.append(self._audio_dir)
            
            if temp_dirs:
                self._file_manager.cleanup_temp_directories(
                    temp_dirs
                )
        except Exception as e:
            logging.error(f"Error cleaning up: {e}")
    
    def _generate_chunk_audio(
            self,
            chunk: AudioChunk,
            language: str,
            gender: str
    ) -> None:
        """
        Generates audio for a single chunk with retry logic.
        """
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
            # Don't emit error immediately to avoid spamming, just log it. 
            # The final check will report failures.
    
    def _update_progress_display(
            self
    ) -> None:
        """Updates the progress display via signal."""
        import time
        current_time = time.time() * 1000
        
        # Throttle signals
        if (current_time - self._last_signal_time) < self._signal_throttle_ms:
            return

        if self._progress_tracker:
            self._last_signal_time = current_time
            completed = self._progress_tracker.get_completed_count()
            total = self._progress_tracker.get_total_count()
            eta = self._progress_tracker.get_eta_string()
            
            self.progressUpdated.emit(
                self._progress_tracker.get_processed_count(),
                total,
                eta
            )
    
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
    
    def _sanitize_filename(self, filename: str) -> str:
        """
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
        """
        Finalizes generation by assembling audio and cleaning up.
        """
        if not self.config:
            return

        logging.info("Finalizing generation...")
        try:
            base_output_dir = Path(self.config.output_dir_path)
            final_dir = base_output_dir / "final"
            self._file_manager.ensure_directory_exists(
                str(final_dir)
            )
            
            # Sanitize filename
            safe_name = self._sanitize_filename(self.config.project_name)
            output_path = final_dir / f"{safe_name}.mp3"
            
        except Exception as e:
            logging.error(f"Failed to prepare output directory: {e}")
            self.errorOccurred.emit(
                f"Failed to prepare output directory: {str(e)}"
            )
            return

        valid_audio_files = [
            f for f in self._audio_files if f is not None
        ]

        if not valid_audio_files:
            logging.error("No valid audio files generated")
            self.errorOccurred.emit(
                "No audio files were generated successfully"
            )
            return
            
        def _assembly_progress_callback(percentage: float, remaining: float) -> None:
             self.assemblyProgressUpdated.emit(percentage, remaining)

        def _assembly_task() -> None:
            try:
                # 1. Ensure all batches are submitted
                self._check_batch_assembly()
                
                # 2. Wait for all batch assemblies to complete
                logging.info("Waiting for background assembly tasks...")
                if self._assembly_executor:
                    self._assembly_executor.shutdown(wait=True)
                
                # 3. Check results
                total_batches = (len(self._chunks) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
                ordered_parts = []
                use_batch_result = True
                
                for i in range(total_batches):
                    if i in self._batch_results:
                        ordered_parts.append(self._batch_results[i])
                    else:
                        logging.warning(f"Batch {i} result missing. Falling back to full assembly.")
                        use_batch_result = False
                        break
                
                if use_batch_result and ordered_parts:
                    logging.info(f"Assembling {len(ordered_parts)} pre-processed parts (Fast mode)")
                    self._audio_assembler.assemble_audio(
                        ordered_parts,
                        str(output_path),
                        speed=1.0,  # Speed already applied in batches
                        callback=_assembly_progress_callback,
                        copy_codec=True # Fast merge
                    )
                else:
                    logging.info(f"Assembling {len(valid_audio_files)} raw chunks (Fallback mode)")
                    self._audio_assembler.assemble_audio(
                        valid_audio_files,
                        str(output_path),
                        speed=self.config.speed,
                        callback=_assembly_progress_callback,
                        copy_codec=False # Full process
                    )
                
                temp_dirs = []
                if self._text_dir:
                    temp_dirs.append(self._text_dir)
                if self._audio_dir:
                    temp_dirs.append(self._audio_dir)
                
                self._file_manager.cleanup_temp_directories(
                    temp_dirs
                )
                
                self.generationCompleted.emit(
                    str(output_path)
                )
                
                failed_count = self._progress_tracker.get_failed_count()
                if failed_count > 0:
                    self.errorOccurred.emit(
                        f"Warning: {failed_count} chunks failed to generate and were skipped."
                    )
                    
            except Exception as e:
                logging.error(f"Assembly failed: {e}", exc_info=True)
                self.errorOccurred.emit(
                    f"Failed to finalize generation: {str(e)}"
                )

        # Run assembly in a separate thread to prevent GUI freezing
        thread = threading.Thread(target=_assembly_task, daemon=True)
        thread.start()
    
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
        self._batch_submitted.add(batch_index)
        
        chunk_files = self._audio_files[start_idx:end_idx]
        output_dir = Path(self._audio_dir)
        part_path = output_dir / f"part_{batch_index}.mp3"
        
        def _assemble_task():
            try:
                # Apply speed here (re-encode)
                self._audio_assembler.assemble_audio(
                    chunk_files,
                    str(part_path),
                    speed=self.config.speed,
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
                # If batch fails, we can't easily recover in this architecture 
                # without complex retry logic. 
                # For now, we log it. Final assembly will detect missing parts.
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
