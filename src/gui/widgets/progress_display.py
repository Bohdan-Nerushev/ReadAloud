"""
Progress display widget.

This module provides visual feedback on audio generation progress.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar
from PyQt6.QtCore import Qt
from src.gui.styles import Styles


class ProgressDisplayWidget(QWidget):
    """
    Widget for displaying generation progress.
    
    Shows a progress bar and status text with completion count and ETA.
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
        layout.setSpacing(Styles.SPACING_MEDIUM)
        
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(Styles.PROGRESS_BAR_HEIGHT)
        self._progress_bar.setStyleSheet(Styles.PROGRESS_BAR_STYLE)
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)
        
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(Styles.LABEL_STATUS)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)
        
        self.setLayout(layout)
    
    def update_progress(
            self,
            completed: int,
            total: int,
            eta: str
    ) -> None:
        """
        Updates the progress display.
        
        Args:
            completed: Number of completed chunks
            total: Total number of chunks
            eta: Estimated time to completion (formatted string)
        """
        if total > 0:
            percentage = int(
                (completed / total) * 100
            )
            self._progress_bar.setValue(percentage)
        
        status_text = f"Progress: {completed}/{total} completed    |    ETA: {eta}"
        self._status_label.setText(status_text)
    
    def reset(
            self
    ) -> None:
        """Resets the progress display to initial state."""
        self._progress_bar.setValue(0)
        self._status_label.setText("")
    
    def set_complete(
            self
    ) -> None:
        """Sets the progress display to show completion."""
        self._progress_bar.setValue(100)
        self._status_label.setText("Generation complete!")

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
