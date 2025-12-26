"""
Project name input widget.

This module provides a widget for entering the project name (output MP3 filename).
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit
from PyQt6.QtCore import pyqtSignal
from src.gui.styles import Styles


class ProjectInputWidget(QWidget):
    """
    Widget for project name input.
    
    Emits projectNameChanged signal when the user modifies the project name.
    """
    
    projectNameChanged = pyqtSignal(str)
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the ProjectInputWidget.
        
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
        
        label = QLabel("Project Name (MP3 filename):")
        label.setStyleSheet(Styles.LABEL_FIELD)
        layout.addWidget(label)
        
        self._input = QLineEdit()
        self._input.setStyleSheet(Styles.INPUT_STYLE)
        self._input.setPlaceholderText("Enter project name...")
        self._input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._input)
        
        self.setLayout(layout)
    
    def _on_text_changed(
            self,
            text: str
    ) -> None:
        """
        Handles text change events.
        
        Args:
            text: New text value
        """
        if text.strip():
            self.projectNameChanged.emit(text.strip())
    
    def get_project_name(
            self
    ) -> str:
        """
        Returns the current project name.
        
        Returns:
            Project name text
        """
        return self._input.text().strip()
    
    def clear(
            self
    ) -> None:
        """Clears the input field."""
        self._input.clear()
