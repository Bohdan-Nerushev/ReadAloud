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
        self._start_button.show()
    
    def set_running_state(
            self
    ) -> None:
        """Sets buttons to running state."""
        self._start_button.setEnabled(True)
    
    def set_paused_state(
            self
    ) -> None:
        """Sets buttons to paused state."""
        pass
    
    def disable_all(
            self
    ) -> None:
        """Disables all buttons."""
        self._start_button.setEnabled(False)
