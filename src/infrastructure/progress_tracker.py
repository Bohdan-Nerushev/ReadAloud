"""
Progress tracking utilities.

This module handles tracking progress of audio generation and estimating completion time.
"""

from datetime import datetime, timedelta
from typing import Optional
import threading


class ProgressTracker:
    """
    Service responsible for tracking progress and estimating completion time.
    
    This service is thread-safe and can be used from multiple threads simultaneously.
    """
    
    def __init__(
            self,
            total_chunks: int
    ) -> None:
        """
        Initialize the ProgressTracker.
        
        Args:
            total_chunks: Total number of chunks to process
            
        Raises:
            ValueError: If total_chunks is not positive
        """
        if total_chunks <= 0:
            raise ValueError(
                f"Total chunks must be positive, got: {total_chunks}"
            )
        
        self._total_chunks: int = total_chunks
        self._completed_chunks: int = 0
        self._failed_chunks: int = 0
        self._start_time: Optional[datetime] = None
        self._lock: threading.Lock = threading.Lock()
    
    def start(
            self
    ) -> None:
        """Records the start time for progress tracking."""
        with self._lock:
            self._start_time = datetime.now()
    
    def update_progress(
            self,
            success: bool
    ) -> None:
        """
        Updates progress counters.
        
        Args:
            success: True if the chunk was processed successfully, False otherwise
        """
        with self._lock:
            if success:
                self._completed_chunks += 1
            else:
                self._failed_chunks += 1
    
    def get_progress_percentage(
            self
    ) -> float:
        """
        Calculates the current progress percentage based on processed items.
        
        Returns:
            Progress percentage (0.0 to 100.0)
        """
        with self._lock:
            if self._total_chunks == 0:
                return 0.0
            processed = self._completed_chunks + self._failed_chunks
            return (processed / self._total_chunks) * 100.0
    
    def get_completed_count(
            self
    ) -> int:
        """
        Returns the number of completed chunks.
        
        Returns:
            Count of completed chunks
        """
        with self._lock:
            return self._completed_chunks
    
    def get_total_count(
            self
    ) -> int:
        """
        Returns the total number of chunks.
        
        Returns:
            Total chunk count
        """
        return self._total_chunks
    
    def get_estimated_completion_time(
            self
    ) -> Optional[datetime]:
        """
        Estimates when the processing will complete.
        
        Calculates based on the average time per chunk and remaining chunks.
        
        Returns:
            Estimated completion datetime, or None if estimation is not possible
        """
        with self._lock:
            processed_count = self._completed_chunks + self._failed_chunks
            if self._start_time is None or processed_count == 0:
                return None
            
            elapsed_time = datetime.now() - self._start_time
            average_time_per_chunk = elapsed_time / processed_count
            
            remaining_chunks = self._total_chunks - processed_count
            estimated_remaining_time = average_time_per_chunk * remaining_chunks
            
            return datetime.now() + estimated_remaining_time
    
    def get_eta_string(
            self
    ) -> str:
        """
        Returns a formatted string representation of the estimated time remaining.
        
        Returns:
            Formatted ETA string (e.g., "00:05:30") or "..." if not available
        """
        eta = self.get_estimated_completion_time()
        if eta is None:
            return "..."
        
        remaining = eta - datetime.now()
        total_seconds = int(remaining.total_seconds())
        
        if total_seconds < 0:
            total_seconds = 0
        
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def get_processed_count(
            self
    ) -> int:
        """
        Returns the number of processed chunks (success + failure).
        
        Returns:
            Count of processed chunks
        """
        with self._lock:
            return self._completed_chunks + self._failed_chunks
    
    def get_failed_count(
            self
    ) -> int:
        """
        Returns the number of failed chunks.
        
        Returns:
            Count of failed chunks
        """
        with self._lock:
            return self._failed_chunks
