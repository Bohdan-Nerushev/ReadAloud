"""
Gender selection widget.

This module provides a dropdown widget for selecting the voice gender.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox
from PyQt6.QtCore import pyqtSignal
from src.gui.styles import Styles


class GenderSelectorWidget(QWidget):
    """
    Widget for voice gender selection.
    
    Emits genderChanged signal when the user selects a different gender.
    """
    
    genderChanged = pyqtSignal(str)
    
    GENDERS = {
        "Male": "male",
        "Female": "female"
    }
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the GenderSelectorWidget.
        
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
        
        label = QLabel("Voice Gender:")
        label.setStyleSheet(Styles.LABEL_FIELD)
        layout.addWidget(label)
        
        self._combo = QComboBox()
        self._combo.setStyleSheet(Styles.INPUT_STYLE)
        for gender_name in self.GENDERS.keys():
            self._combo.addItem(gender_name)
        
        self._combo.currentTextChanged.connect(self._on_gender_changed)
        layout.addWidget(self._combo)
        
        self.setLayout(layout)
    
    def _on_gender_changed(
            self,
            gender_name: str
    ) -> None:
        """
        Handles gender selection change.
        
        Args:
            gender_name: Display name of the selected gender
        """
        gender_code = self.GENDERS.get(
            gender_name,
            "male"
        )
        self.genderChanged.emit(gender_code)
    
    def get_selected_gender(
            self
    ) -> str:
        """
        Returns the currently selected gender code.
        
        Returns:
            Gender code ('male' or 'female')
        """
        gender_name = self._combo.currentText()
        return self.GENDERS.get(
            gender_name,
            "male"
        )
