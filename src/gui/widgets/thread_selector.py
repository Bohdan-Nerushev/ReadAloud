"""
Thread count selector widget.

This module provides a spinner widget for selecting the number of concurrent threads.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSpinBox
from PyQt6.QtCore import pyqtSignal
from src.gui.styles import Styles


class ThreadSelectorWidget(QWidget):
    """
    Widget for thread count selection.
    
    Emits threadCountChanged signal when the user changes the thread count.
    """
    
    threadCountChanged = pyqtSignal(int)
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the ThreadSelectorWidget.
        
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
        layout.setContentsMargins(
            0,
            0,
            0,
            0
        )
        
        label = QLabel("Number of Threads (1-50):")
        label.setStyleSheet(Styles.LABEL_FIELD)
        layout.addWidget(label)
        
        self._spinbox = QSpinBox()
        self._spinbox.setStyleSheet(Styles.INPUT_STYLE)
        self._spinbox.setMinimum(1)
        self._spinbox.setMaximum(50)
        self._spinbox.setValue(1)
        self._spinbox.valueChanged.connect(self._on_value_changed)
        layout.addWidget(self._spinbox)
        
        self.setLayout(layout)
    
    def _on_value_changed(
            self,
            value: int
    ) -> None:
        """
        Handles value change events.
        
        Args:
            value: New thread count value
        """
        self.threadCountChanged.emit(value)
    
    def get_thread_count(
            self
    ) -> int:
        """
        Returns the currently selected thread count.
        
        Returns:
            Thread count (1-50)
        """
        return self._spinbox.value()
