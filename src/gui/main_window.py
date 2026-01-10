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
        
        # Use minimum size instead of fixed size for responsiveness
        self.setMinimumSize(
            Styles.WINDOW_MIN_WIDTH,
            Styles.WINDOW_MIN_HEIGHT
        )
        
        # Main central widget
        main_widget = QWidget()
        main_widget.setStyleSheet(Styles.MAIN_WINDOW_STYLE)
        self.setCentralWidget(main_widget)
        
        # Layout for the main widget
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Scroll Area to handle different resolutions and scaling
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setStyleSheet(Styles.MAIN_WINDOW_STYLE) # Match background
        
        # Content widget inside scroll area
        scroll_content = QWidget()
        scroll_content.setStyleSheet(Styles.MAIN_WINDOW_STYLE)
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(
            Styles.MARGIN,
            Styles.MARGIN,
            Styles.MARGIN,
            Styles.MARGIN
        )
        scroll_layout.setSpacing(Styles.SPACING_LARGE)
        
        # --- UI Components ---
        
        title_label = QLabel("ReadAloud - Text to Speech Generator")
        title_label.setStyleSheet(Styles.LABEL_TITLE)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll_layout.addWidget(title_label)
        
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
        
        scroll_layout.addWidget(config_card)
        
        # Progress area
        self.progress_display = ProgressDisplayWidget()
        scroll_layout.addWidget(self.progress_display)
        
        # Add stretch to push content up if window is tall
        scroll_layout.addStretch()
        
        # Set scroll content
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)
        
        # Bottom controls (outside scroll area? No, maybe inside is better if window is small)
        # However, usually buttons should be always visible. 
        # But if the window is very small, buttons might cover content. 
        # Let's put buttons inside scroll area to be safe against very small screens, 
        # OR put them in a persistent bottom bar. 
        # Given "Adaptive Interface", persistent bottom bar is standard.
        # But for simplicity and safety, putting them inside scroll area guarantees access.
        # Let's try putting them at the bottom of the main layout (outside scroll) for a "sticky footer" feel.
        
        self.control_buttons = ControlButtonsWidget()
        # Add some padding/margin for the footer
        footer_container = QWidget()
        footer_container.setStyleSheet(Styles.MAIN_WINDOW_STYLE)
        footer_layout = QVBoxLayout(footer_container)
        footer_layout.setContentsMargins(Styles.MARGIN, 10, Styles.MARGIN, Styles.MARGIN)
        footer_layout.addWidget(self.control_buttons)
        
        main_layout.addWidget(footer_container)
    
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
