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
    
    deleteRequested = pyqtSignal(str)
    
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
        # Main Layout (Vertical)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(6)
        
        # Row 1: Project Name | Status | Percentage
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        self.name_label = QLabel(task.config.project_name)
        self.name_label.setStyleSheet(
            f"font-weight: bold; font-size: 15px; color: {Palette.TEXT_PRIMARY};"
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
        layout.addLayout(header_layout)
        
        # Row 2: Output path
        self.path_label = QLabel(task.config.output_dir_path)
        self.path_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.path_label)
        
        # Row 3: Settings
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(12)
        for text in [
            f"Language: {self._get_language_name(task.config.language)}",
            f"Gender: {task.config.gender.capitalize()}",
            f"Speed: {task.config.speed}x",
            f"Threads: {task.config.thread_count}"
        ]:
            l = QLabel(text)
            l.setStyleSheet("color: #555; font-size: 11px;")
            settings_layout.addWidget(l)
        settings_layout.addStretch()
        layout.addLayout(settings_layout)
        
        # Row 4: Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(Styles.QUEUE_ITEM_PROGRESS_HEIGHT)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {Palette.BG_MAIN};
                border: 1px solid {Palette.BORDER_LIGHT};
                border-radius: 4px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {Palette.PRIMARY};
                border-radius: 3px;
            }}
        """)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(task.progress))
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Row 5: Message & Buttons
        bottom_layout = QHBoxLayout()
        
        self.message_label = QLabel(task.message)
        self.message_label.setStyleSheet("color: #777; font-size: 11px; font-style: italic;")
        bottom_layout.addWidget(self.message_label, stretch=1)
        
        # Horizontal Button Row
        buttons_group = QHBoxLayout()
        buttons_group.setSpacing(8)
        
        self.delete_button = QPushButton("X")
        self.delete_button.setFixedSize(30, 26)
        self.delete_button.setStyleSheet(Styles.BUTTON_STOP + "QPushButton { padding: 0px; }")
        self.delete_button.clicked.connect(lambda: self.deleteRequested.emit(str(self.task_id)))
        buttons_group.addWidget(self.delete_button)
        
        bottom_layout.addLayout(buttons_group)
        layout.addLayout(bottom_layout)
        
        # Overall Style
        self.setFixedHeight(Styles.QUEUE_ITEM_HEIGHT)
        self.setStyleSheet(f"""
            QueueItemWidget {{
                background-color: {Palette.BG_INPUT};
                border: 1px solid {Palette.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)
        
        self._update_button_states(task)
        
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
        
        self._update_button_states(task)

    def _update_button_states(self, task: GenerationTask) -> None:
        """Enables/disables buttons based on task status."""
        # Delete works for everything
        self.delete_button.setEnabled(True)
        
        # Ensure they are always visible as per user's request for the second task
        self.delete_button.show()
        
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
        elif status == TaskStatus.PAUSED:
            color = Palette.WARNING
            
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
