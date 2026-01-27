"""
Queue service for managing generation tasks.
"""

import logging
from collections import deque
from typing import Optional, List, Callable
from src.domain.models import GenerationTask, TaskStatus, ProjectConfig


class QueueService:
    """
    Service responsible for managing the queue of audio generation tasks.
    """

    def __init__(
            self
    ) -> None:
        """Initialize the QueueService."""
        self._task_queue: deque[GenerationTask] = deque()
        self._current_task: Optional[GenerationTask] = None
        self._on_task_added_callbacks: List[Callable[[GenerationTask], None]] = []
        self._on_task_updated_callbacks: List[Callable[[GenerationTask], None]] = []
        self._on_status_changed_callbacks: List[Callable[[bool], None]] = []

    def add_task(
            self,
            config: ProjectConfig
    ) -> GenerationTask:
        """
        Creates and adds a new task to the queue.

        Args:
            config: Project configuration

        Returns:
            The created GenerationTask
        """
        task = GenerationTask(
            config=config
        )
        self._task_queue.append(task)
        logging.info(f"Task added to queue: {task.id} (Project: {config.project_name})")
        
        for callback in self._on_task_added_callbacks:
            callback(task)
            
        return task

    def get_next_task(
            self
    ) -> Optional[GenerationTask]:
        """
        Retrieves the next task from the queue if idle.

        Returns:
            The next task to process, or None if busy or queue empty
        """
        if self._current_task is not None:
            return None

        if not self._task_queue:
            for callback in self._on_status_changed_callbacks:
                callback(False)
            return None

        for callback in self._on_status_changed_callbacks:
            callback(True)
            
        self._current_task = self._task_queue.popleft()
        return self._current_task

    def finalize_current_task(
            self
    ) -> None:
        """Clears the current task reference."""
        self._current_task = None

    def update_task_progress(
            self,
            progress: float
    ) -> None:
        """Updates progress of the current task."""
        if self._current_task:
            self._current_task.update_progress(progress)
            self._notify_task_updated(self._current_task)

    def update_task_status(
            self,
            status: TaskStatus,
            message: str = ""
    ) -> None:
        """Updates status of the current task."""
        if self._current_task:
            self._current_task.update_status(
                status,
                message
            )
            self._notify_task_updated(self._current_task)

    def get_current_task(
            self
    ) -> Optional[GenerationTask]:
        """Returns the current task being processed."""
        return self._current_task

    def remove_task(
            self,
            task_id: str
    ) -> bool:
        """
        Removes a task from the queue by ID.
        
        Args:
            task_id: ID of the task to remove
            
        Returns:
            True if removed, False otherwise
        """
        for i, task in enumerate(self._task_queue):
            if str(task.id) == task_id:
                # Remove from deque
                del self._task_queue[i]
                logging.info(f"Task {task_id} removed from queue.")
                return True
        return False

    def clear_all_tasks(self) -> None:
        """Removes all tasks and notifies about status change."""
        self._task_queue.clear()
        self._current_task = None
        logging.info("All tasks cleared from queue.")
        for callback in self._on_status_changed_callbacks:
            callback(False)

    def get_all_tasks(self) -> List[GenerationTask]:
        """Returns all tasks including current and pending."""
        tasks = []
        if self._current_task:
            tasks.append(self._current_task)
        tasks.extend(list(self._task_queue))
        return tasks

    def subscribe_task_added(
            self,
            callback: Callable[[GenerationTask], None]
    ) -> None:
        """Subscribes to task added events."""
        self._on_task_added_callbacks.append(callback)

    def subscribe_task_updated(
            self,
            callback: Callable[[GenerationTask], None]
    ) -> None:
        """Subscribes to task updated events."""
        self._on_task_updated_callbacks.append(callback)

    def subscribe_status_changed(
            self,
            callback: Callable[[bool], None]
    ) -> None:
        """Subscribes to queue activity status changes."""
        self._on_status_changed_callbacks.append(callback)

    def _notify_task_updated(
            self,
            task: GenerationTask
    ) -> None:
        """Notifies all subscribers about task updates."""
        for callback in self._on_task_updated_callbacks:
            callback(task)
