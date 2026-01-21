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
        self._user_modified = False
        self._setup_ui()
    
    def _setup_ui(
            self
    ) -> None:
        """Sets up the user interface components."""
        layout = QVBoxLayout()
        layout.setSpacing(Styles.SPACING_SMALL)
        layout.setContentsMargins(
            0,
            0,
            0,
            0
        )
        
        label = QLabel("Project Name (MP3 filename):")
        label.setStyleSheet(Styles.LABEL_FIELD)
        layout.addWidget(label)
        
        self._input = QLineEdit()
        self._input.setStyleSheet(Styles.INPUT_STYLE)
        self._input.setPlaceholderText("Enter project name...")
        self._input.textEdited.connect(self._on_text_edited)
        layout.addWidget(self._input)
        
        self.setLayout(layout)
    
    def _on_text_edited(
            self,
            text: str
    ) -> None:
        """
        Handles manual text editing by user.
        
        Args:
            text: New text value
        """
        self._user_modified = True
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
    
    def set_project_name(
            self,
            name: str
    ) -> None:
        """
        Sets the project name programmatically.
        
        Args:
            name: Project name to set
        """
        self._user_modified = False
        self._input.setText(name)
    
    def is_user_modified(
            self
    ) -> bool:
        """
        Returns whether the user has manually modified the project name.
        
        Returns:
            True if user has modified the name, False otherwise
        """
        return self._user_modified
    
    def clear(
            self
    ) -> None:
        """Clears the input field."""
        self._user_modified = False
        self._input.clear()
