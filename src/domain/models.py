"""
Domain models for the ReadAloud application.

This module contains immutable value objects representing core domain concepts.
"""

from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass(frozen=True)
class ProjectConfig:
    """
    Configuration for a text-to-speech project.
    
    Attributes:
        project_name: Name of the output MP3 file (without extension)
        input_file_path: Path to the input text file
        language: Language code for speech synthesis (en, uk, de, ru)
        thread_count: Number of concurrent threads for audio generation (1-5)
    """
    project_name: str
    input_file_path: str
    language: str
    gender: str
    thread_count: int
    output_dir_path: str

    def __post_init__(
            self
    ) -> None:
        """
        Validates the configuration after initialization.
        
        Raises:
            ValueError: If any validation constraint is violated
        """
        if not self.project_name or not self.project_name.strip():
            raise ValueError(
                "Project name cannot be empty"
            )
        
        if not self.input_file_path:
            raise ValueError(
                "Input file path cannot be empty"
            )
        
        input_path = Path(
            self.input_file_path
        )
        if not input_path.exists():
            raise ValueError(
                f"Input file does not exist: {self.input_file_path}"
            )
        
        if not input_path.is_file():
            raise ValueError(
                f"Input path is not a file: {self.input_file_path}"
            )
        
        allowed_languages = {"en", "uk", "de", "ru"}
        if self.language not in allowed_languages:
            raise ValueError(
                f"Language must be one of {allowed_languages}, got: {self.language}"
            )
        
        allowed_genders = {"male", "female"}
        if self.gender not in allowed_genders:
            raise ValueError(
                f"Gender must be one of {allowed_genders}, got: {self.gender}"
            )
        
        if not 1 <= self.thread_count <= 15:
            raise ValueError(
                f"Thread count must be between 1 and 15, got: {self.thread_count}"
            )
            
        if not self.output_dir_path:
            raise ValueError(
                "Output directory path cannot be empty"
            )
            
        output_dir = Path(self.output_dir_path)
        if not output_dir.exists():
            raise ValueError(
                f"Output directory does not exist: {self.output_dir_path}"
            )
        
        if not output_dir.is_dir():
            raise ValueError(
                f"Output path is not a directory: {self.output_dir_path}"
            )


@dataclass(frozen=True)
class AudioChunk:
    """
    Represents a numbered chunk of text to be converted to audio.
    
    Attributes:
        chunk_number: Sequential number of this chunk (1-indexed)
        text_content: The text content of this chunk
        text_file_path: Optional path where this chunk's text is saved
        audio_file_path: Optional path where this chunk's audio is saved
    """
    chunk_number: int
    text_content: str
    text_file_path: Optional[str] = None
    audio_file_path: Optional[str] = None

    def __post_init__(
            self
    ) -> None:
        """
        Validates the chunk after initialization.
        
        Raises:
            ValueError: If any validation constraint is violated
        """
        if self.chunk_number < 1:
            raise ValueError(
                f"Chunk number must be positive, got: {self.chunk_number}"
            )
        
        if not self.text_content:
            raise ValueError(
                "Text content cannot be empty"
            )
    
    def with_text_path(
            self,
            path: str
    ) -> 'AudioChunk':
        """
        Creates a new AudioChunk with the specified text file path.
        
        Args:
            path: Path to the saved text file
            
        Returns:
            New AudioChunk instance with updated text_file_path
        """
        return AudioChunk(
            chunk_number=self.chunk_number,
            text_content=self.text_content,
            text_file_path=path,
            audio_file_path=self.audio_file_path
        )
    
    def with_audio_path(
            self,
            path: str
    ) -> 'AudioChunk':
        """
        Creates a new AudioChunk with the specified audio file path.
        
        Args:
            path: Path to the saved audio file
            
        Returns:
            New AudioChunk instance with updated audio_file_path
        """
        return AudioChunk(
            chunk_number=self.chunk_number,
            text_content=self.text_content,
            text_file_path=self.text_file_path,
            audio_file_path=path
        )
