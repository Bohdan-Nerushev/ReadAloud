"""
Thread management utilities.

This module handles multi-threaded task execution with pause/resume/stop functionality.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, List
from enum import Enum


class ThreadState(Enum):
    """Represents the state of the thread manager."""
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class ThreadManager:
    """
    Service responsible for managing multi-threaded task execution.
    
    Provides thread pooling with configurable worker count and state management
    for pause/resume/stop operations.
    """
    
    def __init__(
            self,
            thread_count: int
    ) -> None:
        """
        Initialize the ThreadManager.
        
        Args:
            thread_count: Number of worker threads (1-50)
            
        Raises:
            ValueError: If thread_count is not between 1 and 30
        """
        if not 1 <= thread_count <= 50:
            raise ValueError(
                f"Thread count must be between 1 and 50, got: {thread_count}"
            )
        
        self._thread_count: int = thread_count
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=thread_count
        )
        self._state: ThreadState = ThreadState.IDLE
        self._state_lock: threading.Lock = threading.Lock()
        self._pause_event: threading.Event = threading.Event()
        self._pause_event.set()
        self._futures: List[Future] = []
    
    def submit_task(
            self,
            task: Callable[..., Any],
            *args: Any,
            **kwargs: Any
    ) -> Future:
        """
        Submits a task for execution in the thread pool.
        
        Args:
            task: Callable to execute
            *args: Positional arguments for the callable
            **kwargs: Keyword arguments for the callable
            
        Returns:
            Future object representing the task execution
        """
        def wrapped_task(
                *task_args: Any,
                **task_kwargs: Any
        ) -> Any:
            self._pause_event.wait()
            
            with self._state_lock:
                if self._state == ThreadState.STOPPED:
                    return None
            
            return task(*task_args, **task_kwargs)
        
        future = self._executor.submit(
            wrapped_task,
            *args,
            **kwargs
        )
        self._futures.append(future)
        return future
    
    def start(
            self
    ) -> None:
        """Starts or resumes task execution."""
        with self._state_lock:
            self._state = ThreadState.RUNNING
            self._pause_event.set()
    
    def pause(
            self
    ) -> None:
        """Pauses task execution without canceling tasks."""
        with self._state_lock:
            if self._state == ThreadState.RUNNING:
                self._state = ThreadState.PAUSED
                self._pause_event.clear()
    
    def resume(
            self
    ) -> None:
        """Resumes paused task execution."""
        with self._state_lock:
            if self._state == ThreadState.PAUSED:
                self._state = ThreadState.RUNNING
                self._pause_event.set()
    
    def stop(
            self
    ) -> None:
        """Stops all task execution and shuts down the thread pool."""
        with self._state_lock:
            self._state = ThreadState.STOPPED
            self._pause_event.set()
        
        for future in self._futures:
            future.cancel()
        
        self._executor.shutdown(
            wait=False,
            cancel_futures=True
        )
    
    def get_state(
            self
    ) -> ThreadState:
        """
        Returns the current state of the thread manager.
        
        Returns:
            Current ThreadState
        """
        with self._state_lock:
            return self._state
    
    def is_paused(
            self
    ) -> bool:
        """
        Checks if the thread manager is currently paused.
        
        Returns:
            True if paused, False otherwise
        """
        with self._state_lock:
            return self._state == ThreadState.PAUSED
    
    def shutdown(
            self
    ) -> None:
        """Gracefully shuts down the thread pool, waiting for tasks to complete."""
        self._executor.shutdown(wait=True)
