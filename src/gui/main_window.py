"""
Main application window.

This module defines the primary GUI window that assembles all widgets.
"""

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QLabel, QScrollArea, QHBoxLayout
from PyQt6.QtCore import Qt
from src.gui.widgets.project_input import ProjectInputWidget
from src.gui.widgets.file_selector import FileSelectorWidget
from src.gui.widgets.language_selector import LanguageSelectorWidget
from src.gui.widgets.thread_selector import ThreadSelectorWidget
from src.gui.widgets.gender_selector import GenderSelectorWidget
from src.gui.widgets.speed_selector import SpeedSelectorWidget
from src.gui.widgets.progress_display import ProgressDisplayWidget
from src.gui.widgets.control_buttons import ControlButtonsWidget
from src.gui.widgets.output_selector import OutputSelectorWidget
from src.gui.widgets.queue_list import QueueListWidget
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
        self._connect_signals()
    
    def _setup_ui(
            self
    ) -> None:
        """Sets up the user interface components."""
        self.setWindowTitle("ReadAloud - Text to Speech")
        
        # Set fixed window size for predictable layout
        self.setFixedSize(
            Styles.WINDOW_WIDTH,
            Styles.WINDOW_HEIGHT
        )
        
        # Main central widget
        main_widget = QWidget()
        main_widget.setStyleSheet(Styles.MAIN_WINDOW_STYLE)
        self.setCentralWidget(main_widget)
        
        # Main layout
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(
            Styles.MARGIN,
            Styles.MARGIN,
            Styles.MARGIN,
            Styles.MARGIN
        )
        main_layout.setSpacing(Styles.SPACING_LARGE)
        
        # --- UI Components ---
        
        title_label = QLabel("ReadAloud - Text to Speech Generator")
        title_label.setStyleSheet(Styles.LABEL_TITLE)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)
        
        # Configuration Card
        config_card = QWidget()
        config_card.setObjectName("ConfigCard")
        config_card.setStyleSheet(Styles.CARD_STYLE)
        card_layout = QVBoxLayout(config_card)
        card_layout.setContentsMargins(
            Styles.MARGIN, 
            Styles.MARGIN, 
            Styles.MARGIN, 
            Styles.MARGIN
        )
        card_layout.setSpacing(Styles.SPACING_LARGE)
        
        self.project_input = ProjectInputWidget()
        card_layout.addWidget(self.project_input)
        
        self.file_selector = FileSelectorWidget()
        card_layout.addWidget(self.file_selector)
        
        self.output_selector = OutputSelectorWidget()
        card_layout.addWidget(self.output_selector)
        
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(Styles.SPACING_LARGE)
        
        self.language_selector = LanguageSelectorWidget()
        settings_layout.addWidget(self.language_selector)
        
        self.gender_selector = GenderSelectorWidget()
        settings_layout.addWidget(self.gender_selector)
        
        self.speed_selector = SpeedSelectorWidget()
        settings_layout.addWidget(self.speed_selector)
        
        self.thread_selector = ThreadSelectorWidget()
        settings_layout.addWidget(self.thread_selector)
        
        card_layout.addLayout(settings_layout)
        
        main_layout.addWidget(config_card)
        
        # Progress area
        self.progress_display = ProgressDisplayWidget()
        main_layout.addWidget(self.progress_display)
        
        # Queue List
        self.queue_list = QueueListWidget()
        main_layout.addWidget(self.queue_list)
        
        # Control Buttons at bottom
        self.control_buttons = ControlButtonsWidget()
        main_layout.addWidget(self.control_buttons)
    
    def _connect_signals(
            self
    ) -> None:
        """Connects internal widget signals."""
        self.file_selector.fileBasenameExtracted.connect(self._on_file_basename_extracted)
    
    def _on_file_basename_extracted(
            self,
            basename: str
    ) -> None:
        """
        Handles file basename extraction signal.
        
        Args:
            basename: Extracted file basename without extension
        """
        if not self.project_input.is_user_modified():
            self.project_input.set_project_name(basename)
    
    def set_inputs_enabled(
            self,
            enabled: bool
    ) -> None:
        """
        Enables or disables all input widgets.
        
        Args:
            enabled: True to enable, False to disable
        """
        self.project_input.setEnabled(enabled)
        self.file_selector.setEnabled(enabled)
        self.output_selector.setEnabled(enabled)
        self.language_selector.setEnabled(enabled)
        self.gender_selector.setEnabled(enabled)
        self.speed_selector.setEnabled(enabled)
        self.thread_selector.setEnabled(enabled)
