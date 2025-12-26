"""
Output directory selector widget.

This module provides a widget for selecting the output directory for generated files.
"""

from pathlib import Path
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFileDialog
from PyQt6.QtCore import pyqtSignal, Qt
from src.gui.styles import Styles


class OutputSelectorWidget(QWidget):
    """
    Widget for selecting output directory.
    
    Emits outputDirSelected signal when a directory is chosen.
    """
    
    outputDirSelected = pyqtSignal(str)
    
    DEFAULT_PATH = "/home/bnerushev/Schreibtisch"
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the OutputSelectorWidget.
        
        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self._selected_dir: str = self.DEFAULT_PATH
        self._setup_ui()
    
    def _setup_ui(
            self
    ) -> None:
        """Sets up the user interface components."""
        layout = QVBoxLayout()
        layout.setSpacing(Styles.SPACING_SMALL)
        
        label = QLabel("Output Directory (Result folder):")
        label.setStyleSheet(Styles.LABEL_FIELD)
        layout.addWidget(label)
        
        dir_row = QHBoxLayout()
        dir_row.setSpacing(Styles.SPACING_MEDIUM)
        
        self._dir_label = QLabel(self.DEFAULT_PATH)
        self._update_label_style()
        dir_row.addWidget(self._dir_label, stretch=1)
        
        self._select_button = QPushButton("Browse...")
        self._select_button.setStyleSheet(Styles.BUTTON_FILE_SELECT)
        self._select_button.clicked.connect(self._on_browse_clicked)
        dir_row.addWidget(self._select_button)
        
        layout.addLayout(dir_row)
        
        self.setLayout(layout)
    
    def _update_label_style(self) -> None:
        """Updates the label text with elision and style."""
        metrics = self._dir_label.fontMetrics()
        elided_path = metrics.elidedText(
            self._selected_dir, 
            Qt.TextElideMode.ElideRight, 
            self._dir_label.width() - 10 if self._dir_label.width() > 0 else 300
        )
        self._dir_label.setText(elided_path)
        self._dir_label.setToolTip(self._selected_dir)
        self._dir_label.setStyleSheet(Styles.LABEL_FILE_DISPLAY)

    def resizeEvent(self, event) -> None:
        """Handle resize events to update elided text."""
        self._update_label_style()
        super().resizeEvent(event)
    
    def _on_browse_clicked(
            self
    ) -> None:
        """Handles browse button click event."""
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "Select Output Directory",
            self._selected_dir,
            QFileDialog.Option.ShowDirsOnly
        )
        
        if dir_path:
            self._selected_dir = dir_path
            self._update_label_style()
            self.outputDirSelected.emit(dir_path)
    
    def get_selected_directory(
            self
    ) -> str:
        """
        Returns the path to the selected directory.
        
        Returns:
            Directory path
        """
        return self._selected_dir
