"""
Playback speed selection widget.

This module provides a dropdown widget for selecting the audio playback speed.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox
from PyQt6.QtCore import pyqtSignal
from src.gui.styles import Styles


class SpeedSelectorWidget(QWidget):
    """
    Widget for playback speed selection.
    
    Emits speedChanged signal when the user selects a different speed.
    """
    
    speedChanged = pyqtSignal(float)
    
    SPEEDS = ["1.0", "1.25", "1.5", "1.75", "2.0"]
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the SpeedSelectorWidget.
        
        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(
            self
    ) -> None:
        """Sets up the user interface components."""
        layout = QVBoxLayout()
        layout.setSpacing(Styles.SPACING_SMALL)
        
        label = QLabel("Playback Speed:")
        label.setStyleSheet(Styles.LABEL_FIELD)
        layout.addWidget(label)
        
        self._combo = QComboBox()
        self._combo.setStyleSheet(Styles.INPUT_STYLE)
        self._combo.addItems(self.SPEEDS)
        
        self._combo.currentTextChanged.connect(self._on_speed_changed)
        layout.addWidget(self._combo)
        
        self.setLayout(layout)
    
    def _on_speed_changed(
            self,
            speed_text: str
    ) -> None:
        """
        Handles speed selection change.
        
        Args:
            speed_text: Selected speed as text
        """
        try:
            speed = float(speed_text)
            self.speedChanged.emit(speed)
        except ValueError:
            pass
    
    def get_selected_speed(
            self
    ) -> float:
        """
        Returns the currently selected playback speed.
        
        Returns:
            Playback speed as float
        """
        try:
            return float(self._combo.currentText())
        except ValueError:
            return 1.0
