"""
File management utilities.

This module handles file system operations including directory creation, file saving, and cleanup.
"""

import shutil
from pathlib import Path
from datetime import datetime
from functools import lru_cache
from src.domain.models import AudioChunk


class FileManager:
    """
    Service responsible for file and directory management operations.
    
    Provides utilities for creating timestamped directories, saving text chunks,
    and cleaning up temporary files.
    """
    
    def __init__(
            self
    ) -> None:
        """Initialize the FileManager."""
        pass

    @lru_cache(maxsize=128)
    def _get_path(self, path_str: str) -> Path:
        """Cached conversion of string to absolute Path object with expanded tildes."""
        return Path(path_str).expanduser().resolve()
    
    def create_timestamped_dir(
            self,
            base_name: str,
            parent_dir: str
    ) -> str:
        """
        Creates a directory with a timestamp prefix.
        
        The directory name format is: {timestamp}_{base_name}
        Example: 20231226_171530_text
        
        Args:
            base_name: Base name for the directory (e.g., 'text', 'audio')
            parent_dir: Parent directory where the new directory will be created
            
        Returns:
            Absolute path to the created directory
            
        Raises:
            ValueError: If base_name is empty or parent_dir doesn't exist
        """
        if not base_name or not base_name.strip():
            raise ValueError(
                "Base name cannot be empty"
            )
        
        parent_path = self._get_path(parent_dir)
        if not parent_path.exists():
            raise ValueError(
                f"Parent directory does not exist: {parent_dir}"
            )
        
        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )
        dir_name = f"{timestamp}_{base_name}"
        new_dir_path = parent_path / dir_name
        
        new_dir_path.mkdir(
            parents=True,
            exist_ok=True
        )
        
        return str(new_dir_path.absolute())
    
    def save_text_chunk(
            self,
            chunk: AudioChunk,
            directory: str
    ) -> str:
        """
        Saves a text chunk to a file in the specified directory.
        
        The file is named using the chunk number: {chunk_number}.txt
        
        Args:
            chunk: The AudioChunk to save
            directory: Directory where the file will be saved
            
        Returns:
            Absolute path to the saved file
            
        Raises:
            ValueError: If chunk is None or directory doesn't exist
        """
        if chunk is None:
            raise ValueError(
                "Chunk cannot be None"
            )
        
        dir_path = self._get_path(directory)
        if not dir_path.exists():
            raise ValueError(
                f"Directory does not exist: {directory}"
            )
        
        filename = f"{chunk.chunk_number}.txt"
        file_path = dir_path / filename
        
        # Adaptive buffer size based on content length
        # Default to 8KB, max 64KB, or 2x content size
        content_len = len(chunk.text_content)
        buffer_size = max(
            8192,
            min(content_len * 2, 65536)
        )
        
        with open(
                file_path,
                'w',
                encoding='utf-8',
                buffering=buffer_size
        ) as f:
            f.write(chunk.text_content)
        
        return str(file_path.absolute())
    
    def cleanup_temp_directories(
            self,
            directories: list[str]
    ) -> None:
        """
        Removes temporary directories and their contents.
        
        Args:
            directories: List of directory paths to remove
            
        Raises:
            Exception: If deletion fails for any directory
        """
        for directory in directories:
            dir_path = self._get_path(directory)
            if dir_path.exists() and dir_path.is_dir():
                try:
                    shutil.rmtree(dir_path)
                except Exception as e:
                    raise Exception(
                        f"Failed to delete directory {directory}: {str(e)}"
                    ) from e
    
    def ensure_directory_exists(
            self,
            path: str
    ) -> None:
        """
        Ensures that a directory exists, creating it if necessary.
        
        Args:
            path: Path to the directory
            
        Raises:
            ValueError: If path is empty
        """
        if not path or not path.strip():
            raise ValueError(
                "Path cannot be empty"
            )
        
        dir_path = self._get_path(path)
        dir_path.mkdir(
            parents=True,
            exist_ok=True
        )
