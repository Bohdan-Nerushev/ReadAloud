"""
Progress display widget.

This module provides visual feedback on audio generation progress.
"""

from typing import Optional
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar
from PyQt6.QtCore import Qt
from src.gui.styles import Styles, Palette


class ProgressDisplayWidget(QWidget):
    """
    Widget for displaying generation progress.
    
    Shows a progress bar and status text with completion count, ETA, and active file info.
    """
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the ProgressDisplayWidget.
        
        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._setup_ui()
        self.setFixedHeight(Styles.PROGRESS_WIDGET_HEIGHT)
        self.hide()
    
    def _setup_ui(
            self
    ) -> None:
        """Sets up the user interface components."""
        layout = QVBoxLayout()
        layout.setSpacing(4)
        
        # Currently Voicing File Label
        self._file_label = QLabel("")
        self._file_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {Palette.PRIMARY_PRESSED};"
        )
        self._file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._file_label)

        # Progress Bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(Styles.PROGRESS_BAR_HEIGHT)
        self._progress_bar.setStyleSheet(Styles.PROGRESS_BAR_STYLE)
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)
        
        # Current Task Stats Label
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #333333;")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        # Global Queue Stats Label
        self._global_status_label = QLabel("")
        self._global_status_label.setStyleSheet("font-size: 12px; color: #555; font-style: italic;")
        self._global_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._global_status_label)
        
        self.setLayout(layout)
    
    def update_progress(
            self,
            completed: int,
            total: int,
            eta: str,
            speed: float = 0.0,
            task_name: Optional[str] = None,
            file_path: Optional[str] = None
    ) -> None:
        """
        Updates the progress display.
        
        Args:
            completed: Number of completed chunks
            total: Total number of chunks
            eta: Estimated time to completion (formatted string)
            speed: Current processing speed in chunks/second
            task_name: Optional project name of active task
            file_path: Optional input file path of active task
        """
        if total > 0:
            percentage = int(
                (completed / total) * 100
            )
            self._progress_bar.setValue(percentage)

        display_target = file_path if file_path else (task_name if task_name else "")
        if display_target:
            self._file_label.setText(f"Active File: {display_target}")
            self._file_label.show()
        else:
            self._file_label.hide()
        
        try:
            speed_val = float(speed) if speed is not None else 0.0
            speed_str = f"{speed_val:.1f}"
        except (TypeError, ValueError):
            speed_str = "0.0"
        
        status_text = f"Progress: {completed}/{total} completed    |    Speed: {speed_str} ch/s    |    ETA: {eta}"
        self._status_label.setText(status_text)
    
    def reset(
            self
    ) -> None:
        """Resets the progress display to initial state."""
        self._progress_bar.setValue(0)
        self._file_label.setText("")
        self._file_label.hide()
        self._status_label.setText("")
        self._global_status_label.setText("")
    
    def set_complete(
            self
    ) -> None:
        """Sets the progress display to show completion."""
        self._progress_bar.setValue(100)
        self._status_label.setText("Generation complete!")
        self._global_status_label.setText("")

    def update_assembly_progress(
            self,
            percentage: float,
            remaining_seconds: float
    ) -> None:
        """
        Updates the progress display for assembly phase.
        
        Args:
            percentage: Completion percentage (0-100)
            remaining_seconds: Estimated seconds remaining
        """
        self._progress_bar.setValue(int(percentage))
        
        minutes = int(remaining_seconds // 60)
        seconds = int(remaining_seconds % 60)
        eta_str = f"{minutes:02d}:{seconds:02d}"
        
        self._status_label.setText(f"Assembling audio... {percentage:.1f}% | ETA: {eta_str}")

    def update_global_progress(
            self,
            percentage: float,
            eta: str
    ) -> None:
        """
        Updates the global progress display.
        
        Args:
            percentage: Global completion percentage
            eta: Global estimated time to completion
        """
        self._global_status_label.setText(
            f"Global Queue Progress: {int(percentage)}%    |    Total ETA: {eta}"
        )
