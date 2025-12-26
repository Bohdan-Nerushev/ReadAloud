"""
Application controller.

This module orchestrates the entire application, coordinating between GUI, domain, and infrastructure layers.
"""

from pathlib import Path
from typing import Optional, List
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from src.domain.models import ProjectConfig, AudioChunk
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.infrastructure.file_manager import FileManager
from src.infrastructure.progress_tracker import ProgressTracker
from src.infrastructure.thread_manager import ThreadManager, ThreadState
from src.infrastructure.retry_handler import RetryHandler


class ApplicationController(QObject):
    """
    Central controller that orchestrates audio generation workflow.
    
    Manages application state, coordinates services, and communicates with GUI via signals.
    """
    
    progressUpdated = pyqtSignal(
        int,
        int,
        str
    )
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
        
        self._text_dir: Optional[str] = None
        self._audio_dir: Optional[str] = None
        self._chunks: List[AudioChunk] = []
        self._audio_files: List[str] = []
        
        self._is_stopped = False
        
        self._progress_timer = QTimer()
        self._progress_timer.timeout.connect(self._update_progress_display)
    
    def start_generation(
            self,
            config: ProjectConfig
    ) -> None:
        """
        Starts the audio generation process.
        
        Args:
            config: Project configuration containing all necessary parameters
        """
        try:
            self._is_stopped = False
            
            raw_text = self._read_input_file(
                config.input_file_path
            )
            
            processed_text = self._text_processor.process_text(
                raw_text
            )
            
            self._chunks = self._text_chunker.chunk_text(
                processed_text
            )
            
            workspace_dir = str(
                Path(config.input_file_path).parent
            )
            
            self._text_dir = self._file_manager.create_timestamped_dir(
                "text",
                workspace_dir
            )
            
            self._audio_dir = self._file_manager.create_timestamped_dir(
                "audio",
                workspace_dir
            )
            
            self._save_text_chunks()
            
            self._progress_tracker = ProgressTracker(
                total_chunks=len(self._chunks)
            )
            self._progress_tracker.start()
            
            self._thread_manager = ThreadManager(
                thread_count=config.thread_count
            )
            self._thread_manager.start()
            
            self._audio_files = [None] * len(self._chunks)
            
            for chunk in self._chunks:
                self._thread_manager.submit_task(
                    self._generate_chunk_audio,
                    chunk,
                    config.language
                )
            
            self._progress_timer.start(500)
            
            self._monitor_completion(config)
            
        except Exception as e:
            self.errorOccurred.emit(
                f"Failed to start generation: {str(e)}"
            )
    
    def pause_generation(
            self
    ) -> None:
        """Pauses the ongoing audio generation."""
        if self._thread_manager:
            if self._thread_manager.is_paused():
                self._thread_manager.resume()
            else:
                self._thread_manager.pause()
    
    def stop_generation(
            self
    ) -> None:
        """Stops the audio generation and cleans up temporary files."""
        self._is_stopped = True
        
        if self._thread_manager:
            self._thread_manager.stop()
        
        self._progress_timer.stop()
        
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
        except Exception:
            pass
    
    def _read_input_file(
            self,
            file_path: str
    ) -> str:
        """
        Reads the content of the input text file.
        
        Args:
            file_path: Path to the input file
            
        Returns:
            File content as string
            
        Raises:
            Exception: If file reading fails
        """
        try:
            with open(
                    file_path,
                    'r',
                    encoding='utf-8'
            ) as f:
                return f.read()
        except Exception as e:
            raise Exception(
                f"Failed to read input file: {str(e)}"
            ) from e
    
    def _save_text_chunks(
            self
    ) -> None:
        """Saves all text chunks to individual files."""
        for chunk in self._chunks:
            self._file_manager.save_text_chunk(
                chunk,
                self._text_dir
            )
    
    def _generate_chunk_audio(
            self,
            chunk: AudioChunk,
            language: str
    ) -> None:
        """
        Generates audio for a single chunk with retry logic.
        
        Args:
            chunk: The chunk to convert to audio
            language: Language code for synthesis
        """
        if self._is_stopped:
            return
        
        try:
            audio_path = self._retry_handler.execute_with_retry(
                self._audio_generator.generate_audio,
                chunk,
                language,
                self._audio_dir,
                max_retries=5,
                backoff=2.0
            )
            
            self._audio_files[chunk.chunk_number - 1] = audio_path
            self._progress_tracker.update_progress(success=True)
            
        except Exception as e:
            self._progress_tracker.update_progress(success=False)
            self.errorOccurred.emit(
                f"Failed to generate audio for chunk {chunk.chunk_number}: {str(e)}"
            )
    
    def _update_progress_display(
            self
    ) -> None:
        """Updates the progress display via signal."""
        if self._progress_tracker:
            completed = self._progress_tracker.get_completed_count()
            total = self._progress_tracker.get_total_count()
            eta = self._progress_tracker.get_eta_string()
            
            self.progressUpdated.emit(
                completed,
                total,
                eta
            )
    
    def _monitor_completion(
            self,
            config: ProjectConfig
    ) -> None:
        """
        Monitors generation completion and triggers final assembly.
        
        Args:
            config: Project configuration
        """
        def check_completion() -> None:
            if self._progress_tracker and not self._is_stopped:
                completed = self._progress_tracker.get_completed_count()
                total = self._progress_tracker.get_total_count()
                
                if completed >= total:
                    self._progress_timer.stop()
                    self._finalize_generation(config)
        
        completion_timer = QTimer()
        completion_timer.timeout.connect(check_completion)
        completion_timer.start(1000)
        
        self._completion_timer = completion_timer
    
    def _finalize_generation(
            self,
            config: ProjectConfig
    ) -> None:
        """
        Finalizes generation by assembling audio and cleaning up.
        
        Args:
            config: Project configuration
        """
        try:
            workspace_dir = str(
                Path(config.input_file_path).parent
            )
            final_dir = Path(workspace_dir) / "final"
            self._file_manager.ensure_directory_exists(
                str(final_dir)
            )
            
            output_path = final_dir / f"{config.project_name}.mp3"
            
            valid_audio_files = [
                f for f in self._audio_files if f is not None
            ]
            
            if valid_audio_files:
                self._audio_assembler.assemble_audio(
                    valid_audio_files,
                    str(output_path)
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
            else:
                self.errorOccurred.emit(
                    "No audio files were generated successfully"
                )
                
        except Exception as e:
            self.errorOccurred.emit(
                f"Failed to finalize generation: {str(e)}"
            )
    
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
