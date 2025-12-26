"""
File selection widget.

This module provides a widget for selecting input text files.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog
from PyQt6.QtCore import pyqtSignal, Qt
from src.gui.styles import Styles


class FileSelectorWidget(QWidget):
    """
    Widget for selecting input text files.
    
    Emits fileSelected signal when a file is chosen.
    """
    
    fileSelected = pyqtSignal(str)
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the FileSelectorWidget.
        
        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._selected_file: str = ""
        self._setup_ui()
    
    def _setup_ui(
            self
    ) -> None:
        """Sets up the user interface components."""
        layout = QVBoxLayout()
        layout.setSpacing(Styles.SPACING_SMALL)
        
        label = QLabel("Input Text File:")
        label.setStyleSheet(Styles.LABEL_FIELD)
        layout.addWidget(label)
        
        file_row = QHBoxLayout()
        file_row.setSpacing(Styles.SPACING_MEDIUM)
        
        self._file_label = QLabel("No file selected")
        self._file_label.setStyleSheet(Styles.LABEL_FILE_DISPLAY + "color: #666666; font-style: italic;")
        file_row.addWidget(self._file_label, stretch=1)
        
        self._select_button = QPushButton("Browse...")
        self._select_button.setStyleSheet(Styles.BUTTON_FILE_SELECT)
        self._select_button.clicked.connect(self._on_browse_clicked)
        file_row.addWidget(self._select_button)
        
        layout.addLayout(file_row)
        
        self.setLayout(layout)
    
    def _on_browse_clicked(
            self
    ) -> None:
        """Handles browse button click event."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Text File",
            "",
            "Text Files (*.txt);;All Files (*)"
        )
        
        if file_path:
            self._selected_file = file_path
            
            # Elide long path
            metrics = self._file_label.fontMetrics()
            elided_path = metrics.elidedText(
                file_path, 
                Qt.TextElideMode.ElideRight, 
                self._file_label.width() - 10
            )
            self._file_label.setText(elided_path)
            self._file_label.setToolTip(file_path) # Show full path on hover
            
            self._file_label.setStyleSheet(Styles.LABEL_FILE_DISPLAY)
            self.fileSelected.emit(file_path)
    
    def get_selected_file(
            self
    ) -> str:
        """
        Returns the path to the selected file.
        
        Returns:
            File path, or empty string if no file selected
        """
        return self._selected_file
    
    def clear(
            self
    ) -> None:
        """Clears the file selection."""
        self._selected_file = ""
        self._file_label.setText("No file selected")
        self._file_label.setStyleSheet(Styles.LABEL_FILE_DISPLAY + "color: #999999; font-style: italic;")
