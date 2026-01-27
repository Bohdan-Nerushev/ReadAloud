"""
Control buttons widget.

This module provides Start, Pause, and Stop buttons with appropriate styling.
"""

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton
from PyQt6.QtCore import pyqtSignal
from src.gui.styles import Styles


class ControlButtonsWidget(QWidget):
    """
    Widget containing control buttons for audio generation.
    
    Emits signals when buttons are clicked.
    """
    
    startClicked = pyqtSignal()
    pauseClicked = pyqtSignal()
    stopClicked = pyqtSignal()
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the ControlButtonsWidget.
        
        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(
            self
    ) -> None:
        """Sets up the user interface components."""
        layout = QHBoxLayout()
        layout.setSpacing(Styles.SPACING_MEDIUM)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self._start_button = QPushButton("Add to Queue")
        self._start_button.setStyleSheet(Styles.BUTTON_START)
        self._start_button.clicked.connect(self._on_start_clicked)
        layout.addWidget(self._start_button)
        
        self._pause_button = QPushButton("Pause Generation")
        self._pause_button.setStyleSheet(Styles.BUTTON_PAUSE)
        self._pause_button.clicked.connect(self.pauseClicked.emit)
        self._pause_button.setEnabled(False)
        layout.addWidget(self._pause_button)
        
        self._stop_button = QPushButton("Stop Generation")
        self._stop_button.setStyleSheet(Styles.BUTTON_STOP)
        self._stop_button.clicked.connect(self.stopClicked.emit)
        self._stop_button.setEnabled(False)
        layout.addWidget(self._stop_button)
        
        layout.addStretch()
        
        self.setLayout(layout)
    
    def _on_start_clicked(
            self
    ) -> None:
        """Handles start button click."""
        self.startClicked.emit()
    
    def set_idle_state(
            self
    ) -> None:
        """Sets buttons to idle state."""
        self._start_button.setEnabled(True)
        self._pause_button.setEnabled(False)
        self._pause_button.setText("Pause Generation")
        self._stop_button.setEnabled(False)
    
    def set_running_state(
            self
    ) -> None:
        """Sets buttons to running state."""
        self._start_button.setEnabled(True)
        self._pause_button.setEnabled(True)
        self._pause_button.setText("Pause Generation")
        self._stop_button.setEnabled(True)
    
    def set_paused_state(
            self
    ) -> None:
        """Sets buttons to paused state."""
        self._start_button.setEnabled(True)
        self._pause_button.setEnabled(True)
        self._pause_button.setText("Resume Generation")
        self._stop_button.setEnabled(True)
    
    def disable_all(
            self
    ) -> None:
        """Disables all buttons."""
        self._start_button.setEnabled(False)
        self._pause_button.setEnabled(False)
        self._stop_button.setEnabled(False)
