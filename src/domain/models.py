"""
Domain models for the ReadAloud application.

This module contains immutable value objects representing core domain concepts.
"""

from datetime import datetime
import uuid
import re
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Final
from pathlib import Path
from src.domain.exceptions import ConfigurationException


@dataclass(frozen=True)
class ProjectConfig:
    """
    Configuration for a text-to-speech project.
    """
    project_name: str
    input_file_path: str
    language: str
    gender: str
    thread_count: int
    output_dir_path: str
    speed: float = 1.0

    def __post_init__(
            self
    ) -> None:
        """
        Validates the configuration after initialization.

        Raises:
            ConfigurationException: If any validation constraint is violated
        """
        if not self.project_name or not self.project_name.strip():
            raise ConfigurationException(
                "Project name cannot be empty"
            )

        if not re.match(r'^[\w\-. ]+$', self.project_name):
            raise ConfigurationException(
                "Project name contains invalid characters. Use letters, numbers, spaces, dashes and underscores only."
            )

        if not self.input_file_path:
            raise ConfigurationException(
                "Input file path cannot be empty"
            )

        input_path = Path(
            self.input_file_path
        )
        if not input_path.exists():
            raise ConfigurationException(
                f"Input file does not exist: {self.input_file_path}"
            )

        if not input_path.is_file():
            raise ConfigurationException(
                f"Input path is not a file: {self.input_file_path}"
            )

        allowed_languages: Final = {"en", "uk", "de", "ru"}
        if self.language not in allowed_languages:
            raise ConfigurationException(
                f"Language must be one of {allowed_languages}, got: {self.language}"
            )

        allowed_genders: Final = {"male", "female"}
        if self.gender not in allowed_genders:
            raise ConfigurationException(
                f"Gender must be one of {allowed_genders}, got: {self.gender}"
            )

        if not 1 <= self.thread_count <= 30:
            raise ConfigurationException(
                f"Thread count must be between 1 and 30, got: {self.thread_count}"
            )

        if not 0.5 <= self.speed <= 2.0:
            raise ConfigurationException(
                f"Speed must be between 0.5 and 2.0, got: {self.speed}"
            )

        if not self.output_dir_path:
            raise ConfigurationException(
                "Output directory path cannot be empty"
            )

        output_dir = Path(self.output_dir_path)
        if not output_dir.exists():
            raise ConfigurationException(
                f"Output directory does not exist: {self.output_dir_path}"
            )

        if not output_dir.is_dir():
            raise ConfigurationException(
                f"Output path is not a directory: {self.output_dir_path}"
            )


@dataclass(frozen=True)
class AudioChunk:
    """
    Represents a numbered chunk of text to be converted to audio.
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
            ConfigurationException: If any validation constraint is violated
        """
        if self.chunk_number < 1:
            raise ConfigurationException(
                f"Chunk number must be positive, got: {self.chunk_number}"
            )

        if not self.text_content:
            raise ConfigurationException(
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


class TaskStatus(Enum):
    """Status of a generation task."""
    PENDING = "Pending"
    PROCESSING = "Processing"
    COMPLETED = "Completed"
    FAILED = "Failed"
    STOPPED = "Stopped"


@dataclass
class GenerationTask:
    """
    Represents a queued audio generation task.
    """
    config: ProjectConfig
    id: uuid.UUID = field(
        default_factory=uuid.uuid4
    )
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    message: str = "Waiting..."
    created_at: datetime = field(
        default_factory=datetime.now
    )

    def update_status(
            self,
            status: TaskStatus,
            message: str = ""
    ) -> None:
        """Updates task status and message."""
        self.status = status
        if message:
            self.message = message

    def update_progress(
            self,
            progress: float
    ) -> None:
        """Updates task progress."""
        self.progress = progress
