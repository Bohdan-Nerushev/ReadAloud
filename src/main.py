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

from PyQt6.QtWidgets import QApplication, QMessageBox
from src.gui.main_window import MainWindow
from src.application.app_controller import ApplicationController
from src.domain.models import ProjectConfig
from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_assembler import AudioAssembler
from src.domain.exceptions import ConfigurationException
from src.infrastructure.file_manager import FileManager
from src.infrastructure.retry_handler import RetryHandler
from src.application.services.queue_service import QueueService


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
        self._window.control_buttons.pauseClicked.connect(self._on_pause_clicked)
        self._window.control_buttons.stopClicked.connect(self._on_stop_clicked)

        self._controller.progressUpdated.connect(self._on_progress_updated)
        self._controller.assemblyProgressUpdated.connect(self._on_assembly_progress_updated)
        self._controller.errorOccurred.connect(self._on_error_occurred)

        self._controller.taskAdded.connect(self._window.queue_list.add_task)
        self._controller.taskUpdated.connect(self._window.queue_list.update_task)
        self._controller.queueStatusChanged.connect(self._on_queue_status_changed)

    def _on_start_clicked(
            self
    ) -> None:
        """Handles start button click."""
        try:
            config = self._build_config_from_ui()
            self._controller.add_task(config)

            QMessageBox.information(
                self._window,
                "Task Added",
                f"Task '{config.project_name}' added to queue."
            )
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

    def _on_pause_clicked(
            self
    ) -> None:
        """Handles pause/resume button click."""
        self._controller.pause_generation()
        if self._controller.is_paused():
            self._window.control_buttons.set_paused_state()
        else:
            self._window.control_buttons.set_running_state()

    def _on_stop_clicked(
            self
    ) -> None:
        """Handles stop button click."""
        reply = QMessageBox.question(
            self._window,
            "Confirm Stop",
            "Are you sure you want to stop generation? Temporary files will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._controller.stop_generation()

    def _on_progress_updated(
            self,
            completed: int,
            total: int,
            eta: str
    ) -> None:
        """Handles progress update signals."""
        self._window.progress_display.update_progress(completed, total, eta)

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
        if "Critical" in error_message or "System" in error_message:
            QMessageBox.critical(self._window, "Error", error_message)

    def _on_queue_status_changed(
            self,
            active: bool
    ) -> None:
        """Handles queue activity status changes."""
        if active:
            self._window.control_buttons.set_running_state()
        else:
            self._window.control_buttons.set_idle_state()


def main() -> None:
    """Main entry point for the ReadAloud application."""
    setup_logging()
    logging.info("Starting ReadAloud application")

    app = QApplication(sys.argv)
    app.setApplicationName("ReadAloud")

    if not _check_system_dependencies():
        sys.exit(1)

    try:
        # Dependency Injection Container (Manual)
        queue_service = QueueService()
        text_processor = TextProcessor()
        text_chunker = TextChunker()
        audio_generator = AudioGenerator()
        audio_assembler = AudioAssembler()
        file_manager = FileManager()
        retry_handler = RetryHandler()

        controller = ApplicationController(
            queue_service=queue_service,
            text_processor=text_processor,
            text_chunker=text_chunker,
            audio_generator=audio_generator,
            audio_assembler=audio_assembler,
            file_manager=file_manager,
            retry_handler=retry_handler
        )

        window = MainWindow()
        coordinator = ReadAloudApplication(window, controller)

        app.aboutToQuit.connect(controller.stop_generation)
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
