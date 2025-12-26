"""
Language selection widget.

This module provides a dropdown widget for selecting the speech synthesis language.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox
from PyQt6.QtCore import pyqtSignal
from src.gui.styles import Styles


class LanguageSelectorWidget(QWidget):
    """
    Widget for language selection.
    
    Emits languageChanged signal when the user selects a different language.
    """
    
    languageChanged = pyqtSignal(str)
    
    LANGUAGES = {
        "English": "en",
        "Ukrainian": "uk",
        "German": "de",
        "Russian": "ru"
    }
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the LanguageSelectorWidget.
        
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
        
        label = QLabel("Language:")
        label.setStyleSheet(Styles.LABEL_FIELD)
        layout.addWidget(label)
        
        self._combo = QComboBox()
        self._combo.setStyleSheet(Styles.INPUT_STYLE)
        for language_name in self.LANGUAGES.keys():
            self._combo.addItem(language_name)
        
        self._combo.currentTextChanged.connect(self._on_language_changed)
        layout.addWidget(self._combo)
        
        self.setLayout(layout)
    
    def _on_language_changed(
            self,
            language_name: str
    ) -> None:
        """
        Handles language selection change.
        
        Args:
            language_name: Display name of the selected language
        """
        language_code = self.LANGUAGES.get(
            language_name,
            "en"
        )
        self.languageChanged.emit(language_code)
    
    def get_selected_language(
            self
    ) -> str:
        """
        Returns the currently selected language code.
        
        Returns:
            Language code (e.g., 'en', 'uk', 'de', 'ru')
        """
        language_name = self._combo.currentText()
        return self.LANGUAGES.get(
            language_name,
            "en"
        )
