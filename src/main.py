"""
ReadAloud Application - Main Entry Point.

A text-to-speech application that converts text files to MP3 audio using Edge TTS.
"""

import sys
import logging
from pathlib import Path

# Add project root to sys.path to resolve 'src' package
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.infrastructure.logging_config import setup_logging
from src.infrastructure.system_check import check_dependencies
from src.infrastructure.ioc import Container

from PyQt6.QtWidgets import QApplication, QMessageBox
from src.gui.main_window import MainWindow
from src.application.app_controller import ApplicationController
from src.domain.models import ProjectConfig, TaskStatus, GenerationTask
from src.domain.exceptions import ConfigurationException


class ReadAloudApplication:
    """
    Coordinator class for the ReadAloud application.
    """

    def __init__(
            self,
            window: MainWindow,
            controller: ApplicationController
    ) -> None:
        """Initialize the application coordinator."""
        self._window = window
        self._controller = controller
        self._setup_connections()

    def _setup_connections(
            self
    ) -> None:
        """Sets up signals and slots."""
        self._window.control_buttons.startClicked.connect(self._on_start_clicked)
        self._window.control_buttons.pauseClicked.connect(self._controller.pause_generation)
        self._window.control_buttons.stopClicked.connect(self._controller.stop_generation)

        self._controller.progressUpdated.connect(self._on_progress_updated)
        self._controller.assemblyProgressUpdated.connect(self._on_assembly_progress_updated)
        self._controller.errorOccurred.connect(self._on_error_occurred)
        self._controller.globalProgressUpdated.connect(self._window.progress_display.update_global_progress)

        self._controller.taskAdded.connect(self._window.queue_list.add_task)
        self._controller.taskUpdated.connect(self._window.queue_list.update_task)
        self._controller.taskUpdated.connect(self._on_task_updated)
        self._controller.queueStatusChanged.connect(self._on_queue_status_changed)
        
        # Connect queue list signals (per-task controls)
        # Connect queue list signals (per-task controls)
        self._window.queue_list.taskDeleteRequested.connect(self._on_task_delete_requested)

    def _on_start_clicked(
            self
    ) -> None:
        """Handles start button click."""
        try:
            config = self._build_config_from_ui()
            self._controller.add_task(config)

            logging.info(f"Task '{config.project_name}' added to queue.")
        except ConfigurationException as e:
            QMessageBox.warning(self._window, "Validation Error", str(e))
        except Exception as e:
            logging.error(f"Failed to start task: {e}", exc_info=True)
            QMessageBox.critical(self._window, "Error", f"Internal error: {str(e)}")

    def _build_config_from_ui(
            self
    ) -> ProjectConfig:
        """Reads UI inputs and creates a ProjectConfig."""
        return ProjectConfig(
            project_name=self._window.project_input.get_project_name(),
            input_file_path=self._window.file_selector.get_selected_file(),
            language=self._window.language_selector.get_selected_language(),
            gender=self._window.gender_selector.get_selected_gender(),
            speed=self._window.speed_selector.get_selected_speed(),
            thread_count=self._window.thread_selector.get_thread_count(),
            output_dir_path=self._window.output_selector.get_selected_directory()
        )


    def _on_progress_updated(
            self,
            completed: int,
            total: int,
            eta: str,
            speed: float
    ) -> None:
        """Handles progress update signals."""
        self._window.progress_display.update_progress(completed, total, eta, speed)

    def _on_assembly_progress_updated(
            self,
            percentage: float,
            remaining: float
    ) -> None:
        """Handles assembly progress update signals."""
        self._window.progress_display.update_assembly_progress(percentage, remaining)

    def _on_error_occurred(
            self,
            error_message: str
    ) -> None:
        """Handles error signals."""
        logging.error(f"Error signal received: {error_message}")
        QMessageBox.warning(self._window, "Generation Error", error_message)

    def _on_queue_status_changed(
            self,
            active: bool
    ) -> None:
        """Handles queue activity status changes."""
        if active:
            self._window.control_buttons.set_running_state()
            self._window.progress_display.show()
        else:
            self._window.control_buttons.set_idle_state()
            self._window.progress_display.hide()
            self._window.progress_display.reset()
    

    
    def _on_task_delete_requested(
            self,
            task_id: str
    ) -> None:
        """
        Handles delete/cancel request for a specific task.
        """
        # For non-processing tasks, we might not need a confirmation if it's just removing from queue
        try:
            self._controller.cancel_task(task_id)
            self._window.queue_list.remove_task(task_id)
        except Exception as e:
            logging.error(f"Failed to delete task {task_id}: {e}", exc_info=True)
            QMessageBox.warning(self._window, "Error", f"Failed to delete task: {e}")
    
    def _on_task_updated(
            self,
            task: GenerationTask
    ) -> None:
        """
        Handles task updates and removes completed tasks.
        
        Args:
            task: Updated task
        """
        # Update control buttons based on current task status
        if task.status == TaskStatus.PROCESSING:
            self._window.control_buttons.set_running_state()
        elif task.status == TaskStatus.PAUSED:
            self._window.control_buttons.set_paused_state()
        elif task.status in [TaskStatus.COMPLETED, TaskStatus.STOPPED, TaskStatus.FAILED]:
            # These are handled by _on_queue_status_changed(False) usually, 
            # but we ensure idle state if this task was the last one.
            pass

        # Auto-remove completed tasks after a delay
        if task.status == TaskStatus.COMPLETED:
            # Remove from UI after task is done
            self._window.queue_list.remove_task(str(task.id))


def main() -> None:
    """Main entry point for the ReadAloud application."""
    setup_logging()
    logging.info("Starting ReadAloud application")

    app = QApplication(sys.argv)
    app.setApplicationName("ReadAloud")

    if not _check_system_dependencies():
        sys.exit(1)

    try:
        # Dependency Injection via Container
        container = Container()
        controller = container.app_controller

        window = MainWindow()
        coordinator = ReadAloudApplication(window, controller)

        controller.restore_state()

        app.aboutToQuit.connect(controller.shutdown)
        window.show()


        sys.exit(app.exec())
    except Exception as e:
        logging.critical(f"Failed to initialize: {e}", exc_info=True)
        QMessageBox.critical(None, "Initialization Error", str(e))
        sys.exit(1)


def _check_system_dependencies() -> bool:
    """Checks for required system tools."""
    ok, missing = check_dependencies()
    if not ok:
        error_msg = f"Critical dependencies missing: {', '.join(missing)}"
        QMessageBox.critical(None, "Missing Dependencies", error_msg)
        return False
    return True


if __name__ == "__main__":
    main()
