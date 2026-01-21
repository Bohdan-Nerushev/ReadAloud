"""
Queue item widget.

This module defines the widget used to represent a single task in the queue list.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal
from src.domain.models import GenerationTask, TaskStatus
from src.gui.styles import Styles, Palette

class QueueItemWidget(QWidget):
    """
    Widget representing a single task in the queue.
    """
    
    pauseRequested = pyqtSignal(str)  # task_id
    cancelRequested = pyqtSignal(str)  # task_id
    
    def __init__(
            self,
            task: GenerationTask,
            parent: QWidget = None
    ) -> None:
        """
        Initialize the QueueItemWidget.
        
        Args:
            task: The GenerationTask to display
            parent: Parent widget
        """
        super().__init__(parent)
        self.task_id = task.id
        self._setup_ui(task)
        
    def _setup_ui(
            self,
            task: GenerationTask
    ) -> None:
        """Sets up the UI components."""
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            10,
            10,
            10,
            10
        )
        layout.setSpacing(5)
        
        # Set fixed height for consistent sizing
        self.setFixedHeight(Styles.QUEUE_ITEM_HEIGHT)
        
        # Style
        self.setStyleSheet(f"""
            QueueItemWidget {{
                background-color: {Palette.BG_INPUT};
                border: 1px solid {Palette.BORDER_DEFAULT};
                border-radius: 5px;
            }}
        """)
        
        # Row 1: Project Name | Status | Percentage
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        self.name_label = QLabel(task.config.project_name)
        self.name_label.setStyleSheet(
            "font-weight: bold; font-size: 14px; color: #333;"
        )
        header_layout.addWidget(self.name_label)
        
        header_layout.addStretch()
        
        self.status_label = QLabel(task.status.value)
        self.status_label.setStyleSheet(self._get_status_style(task.status))
        header_layout.addWidget(self.status_label)
        
        self.percentage_label = QLabel(f"{int(task.progress)}%")
        self.percentage_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {Palette.PRIMARY};"
        )
        header_layout.addWidget(self.percentage_label)
        
        # Action buttons
        self.pause_button = QPushButton("⏸")
        self.pause_button.setFixedSize(
            30,
            30
        )
        self.pause_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Palette.WARNING};
                color: {Palette.TEXT_INVERSE};
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {Palette.WARNING_HOVER};
            }}
        """)
        self.pause_button.clicked.connect(
            lambda: self.pauseRequested.emit(str(self.task_id))
        )
        header_layout.addWidget(self.pause_button)
        
        self.cancel_button = QPushButton("✖")
        self.cancel_button.setFixedSize(
            30,
            30
        )
        self.cancel_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {Palette.ERROR};
                color: {Palette.TEXT_INVERSE};
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {Palette.ERROR_HOVER};
            }}
        """)
        self.cancel_button.clicked.connect(
            lambda: self.cancelRequested.emit(str(self.task_id))
        )
        header_layout.addWidget(self.cancel_button)
        
        layout.addLayout(header_layout)
        
        # Row 2: Output path
        self.path_label = QLabel(task.config.output_dir_path)
        self.path_label.setStyleSheet(
            "color: #666; font-size: 11px;"
        )
        layout.addWidget(self.path_label)
        
        # Row 3: Settings (Language, Gender, Speed, Threads)
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(15)
        
        lang_label = QLabel(
            f"Language: {self._get_language_name(task.config.language)}"
        )
        lang_label.setStyleSheet("color: #555; font-size: 11px;")
        settings_layout.addWidget(lang_label)
        
        gender_label = QLabel(f"Gender: {task.config.gender.capitalize()}")
        gender_label.setStyleSheet("color: #555; font-size: 11px;")
        settings_layout.addWidget(gender_label)
        
        speed_label = QLabel(f"Speed: {task.config.speed}x")
        speed_label.setStyleSheet("color: #555; font-size: 11px;")
        settings_layout.addWidget(speed_label)
        
        threads_label = QLabel(f"Threads: {task.config.thread_count}")
        threads_label.setStyleSheet("color: #555; font-size: 11px;")
        settings_layout.addWidget(threads_label)
        
        settings_layout.addStretch()
        
        layout.addLayout(settings_layout)
        
        # Row 4: Progress Bar (fixed height)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(Styles.QUEUE_ITEM_PROGRESS_HEIGHT)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Palette.BG_INPUT};
                border: 1px solid {Palette.TEXT_INVERSE};
                border-radius: 3px;
                text-align: center;
                color: {Palette.TEXT_PRIMARY};
                font-weight: bold;
                font-size: 10px;
            }}
            QProgressBar::chunk {{
                background-color: {Palette.PRIMARY};
                border-radius: 2px;
            }}
        """)
        self.progress_bar.setRange(
            0,
            100
        )
        self.progress_bar.setValue(int(task.progress))
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Row 5: Message
        self.message_label = QLabel(task.message)
        self.message_label.setStyleSheet(
            "color: #555; font-size: 11px; font-style: italic;"
        )
        layout.addWidget(self.message_label)
        
    def update_task(
            self,
            task: GenerationTask
    ) -> None:
        """Updates the widget with new task state."""
        self.status_label.setText(task.status.value)
        self.status_label.setStyleSheet(self._get_status_style(task.status))
        
        self.progress_bar.setValue(int(task.progress))
        self.percentage_label.setText(f"{int(task.progress)}%")
        self.message_label.setText(task.message)
        
    def _get_status_style(
            self,
            status: TaskStatus
    ) -> str:
        """Returns style string for status label."""
        color = Palette.TEXT_SECONDARY
        if status == TaskStatus.PENDING:
            color = Palette.WARNING
        elif status == TaskStatus.PROCESSING:
            color = Palette.PRIMARY
        elif status == TaskStatus.COMPLETED:
            color = Palette.PRIMARY_PRESSED
        elif status == TaskStatus.FAILED:
            color = Palette.ERROR
        elif status == TaskStatus.STOPPED:
            color = Palette.ERROR_HOVER
            
        return f"font-weight: bold; color: {color};"
    
    def _get_language_name(
            self,
            code: str
    ) -> str:
        """Converts language code to readable name."""
        language_map = {
            "en": "English",
            "uk": "Ukrainian",
            "de": "German",
            "ru": "Russian"
        }
        return language_map.get(
            code,
            code
        )
