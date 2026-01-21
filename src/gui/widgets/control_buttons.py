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
        
        self._start_button = QPushButton("Add to Queue")
        self._start_button.setStyleSheet(Styles.BUTTON_START)
        self._start_button.clicked.connect(self._on_start_clicked)
        layout.addWidget(self._start_button)
        
        layout.addStretch()
        
        self._pause_button = QPushButton("Pause")
        self._pause_button.setStyleSheet(Styles.BUTTON_PAUSE)
        self._pause_button.clicked.connect(self._on_pause_clicked)
        self._pause_button.hide()
        layout.addWidget(self._pause_button)
        
        self._stop_button = QPushButton("Stop Generation")
        self._stop_button.setStyleSheet(Styles.BUTTON_STOP)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        self._stop_button.hide()
        layout.addWidget(self._stop_button)
        
        self.setLayout(layout)
    
    def _on_start_clicked(
            self
    ) -> None:
        """Handles start button click."""
        self.startClicked.emit()
    
    def _on_pause_clicked(
            self
    ) -> None:
        """Handles pause button click."""
        self.pauseClicked.emit()
    
    def _on_stop_clicked(
            self
    ) -> None:
        """Handles stop button click."""
        self.stopClicked.emit()
    
    def set_idle_state(
            self
    ) -> None:
        """Sets buttons to idle state (only Start visible)."""
        self._start_button.setEnabled(True)
        self._start_button.show()
        self._pause_button.hide()
        self._stop_button.hide()
    
    def set_running_state(
            self
    ) -> None:
        """Sets buttons to running state (Pause and Stop visible)."""
        self._start_button.show()
        self._start_button.setEnabled(True)
        self._pause_button.setText("Pause")
        self._pause_button.show()
        self._stop_button.show()
    
    def set_paused_state(
            self
    ) -> None:
        """Sets buttons to paused state (Resume and Stop visible)."""
        self._pause_button.setText("Resume")
    
    def disable_all(
            self
    ) -> None:
        """Disables all buttons."""
        self._start_button.setEnabled(False)
        self._pause_button.setEnabled(False)
        self._stop_button.setEnabled(False)
