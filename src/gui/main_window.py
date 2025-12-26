"""
Main application window.

This module defines the primary GUI window that assembles all widgets.
"""

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from src.gui.widgets.project_input import ProjectInputWidget
from src.gui.widgets.file_selector import FileSelectorWidget
from src.gui.widgets.language_selector import LanguageSelectorWidget
from src.gui.widgets.thread_selector import ThreadSelectorWidget
from src.gui.widgets.progress_display import ProgressDisplayWidget
from src.gui.widgets.control_buttons import ControlButtonsWidget
from src.gui.styles import Styles


class MainWindow(QMainWindow):
    """
    Main application window for the ReadAloud application.
    
    Provides a user interface for configuring and executing text-to-speech conversion.
    """
    
    def __init__(
            self
    ) -> None:
        """Initialize the MainWindow."""
        super().__init__()
        self._setup_ui()
    
    def _setup_ui(
            self
    ) -> None:
        """Sets up the user interface components."""
        self.setWindowTitle("ReadAloud - Text to Speech")
        self.setMinimumSize(
            Styles.WINDOW_MIN_WIDTH,
            Styles.WINDOW_MIN_HEIGHT
        )
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(
            Styles.MARGIN,
            Styles.MARGIN,
            Styles.MARGIN,
            Styles.MARGIN
        )
        main_layout.setSpacing(Styles.SPACING_LARGE)
        
        title_label = QLabel("ReadAloud - Text to Speech Generator")
        title_label.setStyleSheet(Styles.LABEL_TITLE)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)
        
        self.project_input = ProjectInputWidget()
        main_layout.addWidget(self.project_input)
        
        self.file_selector = FileSelectorWidget()
        main_layout.addWidget(self.file_selector)
        
        self.language_selector = LanguageSelectorWidget()
        main_layout.addWidget(self.language_selector)
        
        self.thread_selector = ThreadSelectorWidget()
        main_layout.addWidget(self.thread_selector)
        
        self.progress_display = ProgressDisplayWidget()
        main_layout.addWidget(self.progress_display)
        
        main_layout.addStretch()
        
        self.control_buttons = ControlButtonsWidget()
        main_layout.addWidget(self.control_buttons)
        
        central_widget.setLayout(main_layout)
