"""
Queue service for managing generation tasks.
"""

import logging
from collections import deque
from typing import Optional, List, Callable
from PyQt6.QtCore import QObject, pyqtSignal
from src.domain.models import GenerationTask, TaskStatus, ProjectConfig


class QueueService(QObject):
    """
    Service responsible for managing the queue of audio generation tasks.
    """

    taskAdded = pyqtSignal(object)
    taskUpdated = pyqtSignal(object)
    statusChanged = pyqtSignal(bool)

    def __init__(
            self
    ) -> None:
        """Initialize the QueueService."""
        super().__init__()
        self._task_queue: deque[GenerationTask] = deque()
        self._current_task: Optional[GenerationTask] = None
        # Failed/stopped tasks leave the processing flow but must stay reachable
        # so the user can retry or delete them and so they survive a restart.
        self._history: List[GenerationTask] = []

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
        
        self.taskAdded.emit(task)
            
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

        next_task_idx = -1
        for i, task in enumerate(self._task_queue):
            if task.status != TaskStatus.PAUSED:
                next_task_idx = i
                break

        if next_task_idx == -1:
            self.statusChanged.emit(False)
            return None

        self.statusChanged.emit(True)
            
        task = self._task_queue[next_task_idx]
        del self._task_queue[next_task_idx]
        self._current_task = task
        return self._current_task

    def toggle_task_pause(self, task_id: str) -> Optional[GenerationTask]:
        """
        Toggles the pause or retry status of a task in the queue.
        """
        if self._find_in_history(task_id) is not None:
            return self.retry_task(task_id)

        for task in self._task_queue:
            if str(task.id) == task_id:
                if task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.STOPPED):
                    task.update_status(TaskStatus.PENDING, "Pending")
                else:
                    task.update_status(TaskStatus.PAUSED, "Paused")
                self._notify_task_updated(task)
                return task
        return None

    def retry_task(self, task_id: str) -> Optional[GenerationTask]:
        """
        Resets a failed or stopped task to PENDING status and places it at the front of the queue.
        """
        if self._current_task and str(self._current_task.id) == task_id:
            task = self._current_task
            self._current_task = None
            task.update_status(TaskStatus.PENDING, "Pending Retry")
            self._task_queue.appendleft(task)
            self._notify_task_updated(task)
            return task

        history_task = self._find_in_history(task_id)
        if history_task is not None:
            self._history.remove(history_task)
            history_task.update_status(TaskStatus.PENDING, "Pending Retry")
            self._task_queue.appendleft(history_task)
            self._notify_task_updated(history_task)
            return history_task

        for i, task in enumerate(self._task_queue):
            if str(task.id) == task_id:
                task.update_status(TaskStatus.PENDING, "Pending Retry")
                del self._task_queue[i]
                self._task_queue.appendleft(task)
                self._notify_task_updated(task)
                return task

        return None

    def finalize_current_task(
            self
    ) -> None:
        """
        Releases the current task slot.

        A FAILED or STOPPED task is kept in the history registry instead of
        being forgotten, so it can still be retried, deleted and persisted.
        """
        task = self._current_task
        self._current_task = None
        if task is not None and task.status in (TaskStatus.FAILED, TaskStatus.STOPPED):
            if task not in self._history:
                self._history.append(task)

    def discard_current_task(self) -> Optional[GenerationTask]:
        """Drops the current task entirely (user cancellation), without keeping it for retry."""
        task = self._current_task
        self._current_task = None
        return task

    def restore_failed_task(self, task: GenerationTask) -> None:
        """Puts a previously failed/stopped task (loaded from disk) back into the history registry."""
        if task not in self._history:
            self._history.append(task)

    def find_task(self, task_id: str) -> Optional[GenerationTask]:
        """Finds a task by ID among current, queued and failed/stopped tasks."""
        for task in self.get_all_tasks():
            if str(task.id) == task_id:
                return task
        return None

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
        history_task = self._find_in_history(task_id)
        if history_task is not None:
            self._history.remove(history_task)
            logging.info(f"Task {task_id} removed from history.")
            return True
        return False

    def clear_all_tasks(self) -> None:
        """Removes all tasks and notifies about status change."""
        self._task_queue.clear()
        self._history.clear()
        self._current_task = None
        logging.info("All tasks cleared from queue.")
        self.statusChanged.emit(False)

    def get_all_tasks(self) -> List[GenerationTask]:
        """Returns all tasks including current and pending."""
        tasks = []
        if self._current_task:
            tasks.append(self._current_task)
        tasks.extend(list(self._task_queue))
        tasks.extend(self._history)
        return tasks

    def _find_in_history(self, task_id: str) -> Optional[GenerationTask]:
        for task in self._history:
            if str(task.id) == task_id:
                return task
        return None

    def subscribe_task_added(
            self,
            callback: Callable[[GenerationTask], None]
    ) -> None:
        """Subscribes to task added events."""
        self.taskAdded.connect(callback)

    def subscribe_task_updated(
            self,
            callback: Callable[[GenerationTask], None]
    ) -> None:
        """Subscribes to task updated events."""
        self.taskUpdated.connect(callback)

    def subscribe_status_changed(
            self,
            callback: Callable[[bool], None]
    ) -> None:
        """Subscribes to queue activity status changes."""
        self.statusChanged.connect(callback)

    def _notify_task_updated(
            self,
            task: GenerationTask
    ) -> None:
        """Notifies all subscribers about task updates."""
        self.taskUpdated.emit(task)
