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

# Import infrastructure first to setup environment
from src.infrastructure.logging_config import setup_logging
from src.infrastructure.system_check import check_dependencies

from PyQt6.QtWidgets import QApplication, QMessageBox
from src.gui.main_window import MainWindow
from src.application.app_controller import ApplicationController
from src.domain.models import ProjectConfig


def main() -> None:
    """Main entry point for the ReadAloud application."""
    # Setup logging first
    setup_logging()
    logging.info("Starting ReadAloud application")
    
    app = QApplication(sys.argv)
    
    app.setApplicationName("ReadAloud")
    app.setOrganizationName("ReadAloud")
    
    # Check for system dependencies (ffmpeg, ffprobe)
    deps_ok, missing_deps = check_dependencies()
    if not deps_ok:
        missing_str = ", ".join(missing_deps)
        error_msg = (
            f"Critical system dependencies are missing: {missing_str}.\n\n"
            "Please install ffmpeg and ffprobe and ensure they are in your system PATH.\n\n"
            "On Ubuntu/Debian: sudo apt install ffmpeg\n"
            "On macOS: brew install ffmpeg\n"
            "On Windows: Download from ffmpeg.org and add 'bin' folder to PATH."
        )
        logging.critical(f"Missing dependencies: {missing_str}")
        QMessageBox.critical(None, "Missing Dependencies", error_msg)
        sys.exit(1)
    
    try:
        window = MainWindow()
        controller = ApplicationController()
    except Exception as e:
        logging.critical(f"Failed to initialize application: {e}", exc_info=True)
        QMessageBox.critical(None, "Initialization Error", f"Failed to initialize application:\n{e}")
        sys.exit(1)
    
    def on_start_clicked() -> None:
        """Handles start button click."""
        try:
            project_name = window.project_input.get_project_name()
            if not project_name:
                QMessageBox.warning(
                    window,
                    "Validation Error",
                    "Please enter a project name."
                )
                return
            
            input_file = window.file_selector.get_selected_file()
            if not input_file:
                QMessageBox.warning(
                    window,
                    "Validation Error",
                    "Please select an input file."
                )
                return
            
            language = window.language_selector.get_selected_language()
            gender = window.gender_selector.get_selected_gender()
            speed = window.speed_selector.get_selected_speed()
            thread_count = window.thread_selector.get_thread_count()
            output_dir = window.output_selector.get_selected_directory()
            
            config = ProjectConfig(
                project_name=project_name,
                input_file_path=input_file,
                language=language,
                gender=gender,
                speed=speed,
                thread_count=thread_count,
                output_dir_path=output_dir
            )
            
            window.progress_display.reset()
            window.progress_display.show()
            window.control_buttons.set_running_state()
            window.set_inputs_enabled(False)
            
            # Start generation (this is now non-blocking in controller)
            success = controller.start_generation(config)
            if not success:
                window.control_buttons.set_idle_state()
                window.set_inputs_enabled(True)
            
        except ValueError as e:
            logging.error(f"Configuration error: {e}")
            QMessageBox.critical(
                window,
                "Configuration Error",
                str(e)
            )
        except Exception as e:
            logging.error(f"Unexpected error starting generation: {e}", exc_info=True)
            QMessageBox.critical(
                window,
                "Error",
                f"Failed to start generation: {str(e)}"
            )
    
    def on_pause_clicked() -> None:
        """Handles pause/resume button click."""
        controller.pause_generation()
        if controller.is_paused():
            window.control_buttons.set_paused_state()
        else:
            window.control_buttons.set_running_state()
    
    def on_stop_clicked() -> None:
        """Handles stop button click."""
        reply = QMessageBox.question(
            window,
            "Confirm Stop",
            "Are you sure you want to stop generation? Temporary files will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            controller.stop_generation()
            window.progress_display.hide()
            window.control_buttons.set_idle_state()
            window.set_inputs_enabled(True)
    
    def on_progress_updated(
            completed: int,
            total: int,
            eta: str
    ) -> None:
        """Handles progress update signals."""
        window.progress_display.update_progress(
            completed,
            total,
            eta
        )
    
    def on_generation_completed(
            output_path: str
    ) -> None:
        """Handles generation completion."""
        logging.info(f"Generation completed successfully: {output_path}")
        window.progress_display.set_complete()
        window.control_buttons.set_idle_state()
        window.set_inputs_enabled(True)
        
        QMessageBox.information(
            window,
            "Success",
            f"Audio generation completed!\n\nOutput saved to:\n{output_path}"
        )
    
    def on_error_occurred(
            error_message: str
    ) -> None:
        """Handles error signals."""
        logging.error(f"Application error: {error_message}")
        # Only show critical errors if not stopping
        QMessageBox.critical(
            window,
            "Error",
            error_message
        )
        
        # Reset UI state on error
        window.control_buttons.set_idle_state()
        window.set_inputs_enabled(True)

    def on_assembly_progress_updated(
            percentage: float,
            remaining: float
    ) -> None:
        """Handles assembly progress update signals."""
        window.progress_display.update_assembly_progress(
            percentage,
            remaining
        )
    
    app.aboutToQuit.connect(controller.stop_generation)
    
    window.control_buttons.startClicked.connect(on_start_clicked)
    window.control_buttons.pauseClicked.connect(on_pause_clicked)
    window.control_buttons.stopClicked.connect(on_stop_clicked)
    
    controller.progressUpdated.connect(on_progress_updated)
    controller.assemblyProgressUpdated.connect(on_assembly_progress_updated)
    controller.generationCompleted.connect(on_generation_completed)
    controller.errorOccurred.connect(on_error_occurred)
    
    window.show()
    
    exit_code = app.exec()
    logging.info(f"Application exiting with code {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
