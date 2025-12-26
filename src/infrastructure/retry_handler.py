"""
Retry handling utilities.

This module provides automatic retry logic with exponential backoff for operations that may fail.
"""

import time
from typing import Callable, TypeVar, Any, Optional
from functools import wraps


T = TypeVar('T')


class RetryHandler:
    """
    Service responsible for executing operations with automatic retry logic.
    
    Implements exponential backoff to handle transient failures gracefully.
    """
    
    def __init__(
            self
    ) -> None:
        """Initialize the RetryHandler."""
        pass
    
    def execute_with_retry(
            self,
            func: Callable[..., T],
            *args: Any,
            max_retries: int = 3,
            backoff: float = 1.0,
            **kwargs: Any
    ) -> T:
        """
        Executes a function with automatic retry on failure.
        
        Uses exponential backoff: waits backoff * (2 ** attempt) seconds between retries.
        
        Args:
            func: Function to execute
            *args: Positional arguments for the function
            max_retries: Maximum number of retry attempts
            backoff: Initial backoff time in seconds
            **kwargs: Keyword arguments for the function
            
        Returns:
            Return value from the function
            
        Raises:
            ValueError: If max_retries or backoff is not positive
            Exception: If all retry attempts fail, raises the last exception
        """
        if max_retries <= 0:
            raise ValueError(
                f"Max retries must be positive, got: {max_retries}"
            )
        
        if backoff <= 0:
            raise ValueError(
                f"Backoff must be positive, got: {backoff}"
            )
        
        last_exception: Optional[Exception] = None
        
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            
            except Exception as e:
                last_exception = e
                
                if attempt < max_retries - 1:
                    wait_time = backoff * (2 ** attempt)
                    time.sleep(wait_time)
        
        raise last_exception
