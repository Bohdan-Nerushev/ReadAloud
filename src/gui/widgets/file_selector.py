"""
File selection widget.

This module provides a widget for selecting input text files.
"""

from pathlib import Path
from typing import List
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog
from PyQt6.QtCore import pyqtSignal, Qt
from src.gui.styles import Styles


class FileSelectorWidget(QWidget):
    """
    Widget for selecting one or multiple input text files.
    
    Emits fileSelected or filesSelected signals when file(s) are chosen.
    """
    
    fileSelected = pyqtSignal(str)
    filesSelected = pyqtSignal(list)
    fileBasenameExtracted = pyqtSignal(str)
    
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
        self._selected_files: List[str] = []
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
        
        label = QLabel("Input Text File(s):")
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
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Text File(s)",
            "",
            "Text Files (*.txt);;All Files (*)"
        )
        
        if file_paths:
            self._selected_files = file_paths
            metrics = self._file_label.fontMetrics()
            
            if len(file_paths) == 1:
                single_path = file_paths[0]
                elided_path = metrics.elidedText(
                    single_path, 
                    Qt.TextElideMode.ElideRight, 
                    self._file_label.width() - 10
                )
                self._file_label.setText(elided_path)
                self._file_label.setToolTip(single_path)
                self._file_label.setStyleSheet(Styles.LABEL_FILE_DISPLAY)
                
                self.fileSelected.emit(single_path)
                self.filesSelected.emit(file_paths)
                
                file_basename = Path(single_path).stem
                self.fileBasenameExtracted.emit(file_basename)
            else:
                names = [Path(p).name for p in file_paths]
                display_text = f"{len(file_paths)} files selected: {', '.join(names)}"
                elided_text = metrics.elidedText(
                    display_text,
                    Qt.TextElideMode.ElideRight,
                    self._file_label.width() - 10
                )
                self._file_label.setText(elided_text)
                self._file_label.setToolTip("\n".join(file_paths))
                self._file_label.setStyleSheet(Styles.LABEL_FILE_DISPLAY)
                
                self.fileSelected.emit(file_paths[0])
                self.filesSelected.emit(file_paths)
                
                file_basename = Path(file_paths[0]).stem
                self.fileBasenameExtracted.emit(file_basename)
    
    def get_selected_files(
            self
    ) -> List[str]:
        """
        Returns the list of paths to selected files.
        
        Returns:
            List of file paths
        """
        return self._selected_files

    def get_selected_file(
            self
    ) -> str:
        """
        Returns the path to the first selected file.
        
        Returns:
            File path, or empty string if no file selected
        """
        return self._selected_files[0] if self._selected_files else ""
    
    def clear(
            self
    ) -> None:
        """Clears the file selection."""
        self._selected_files = []
        self._file_label.setText("No file selected")
        self._file_label.setToolTip("")
        self._file_label.setStyleSheet(Styles.LABEL_FILE_DISPLAY + "color: #999999; font-style: italic;")
