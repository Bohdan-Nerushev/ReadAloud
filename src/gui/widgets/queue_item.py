"""
Queue item widget.

This module defines the widget used to represent a single task in the queue list.
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar
from PyQt6.QtCore import Qt
from src.domain.models import GenerationTask, TaskStatus
from src.gui.styles import Styles, Palette

class QueueItemWidget(QWidget):
    """
    Widget representing a single task in the queue.
    """
    
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
        
    def _setup_ui(self, task: GenerationTask) -> None:
        """Sets up the UI components."""
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        
        # Style
        self.setStyleSheet(f"""
            QueueItemWidget {{
                background-color: {Palette.BG_INPUT};
                border: 1px solid {Palette.BORDER_DEFAULT};
                border-radius: 5px;
            }}
        """)
        
        # Header: Project Name + Status
        header_layout = QHBoxLayout()
        
        self.name_label = QLabel(task.config.project_name)
        self.name_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #333;")
        header_layout.addWidget(self.name_label)
        
        header_layout.addStretch()
        
        self.status_label = QLabel(task.status.value)
        self.status_label.setStyleSheet(self._get_status_style(task.status))
        header_layout.addWidget(self.status_label)
        
        layout.addLayout(header_layout)
        
        # Details: Output path
        self.path_label = QLabel(task.config.output_dir_path)
        self.path_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.path_label)
        
        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(Styles.PROGRESS_BAR_STYLE.replace("min-height: 35px", "min-height: 15px").replace("max-height: 35px", "max-height: 15px").replace("font-size: 14px", "font-size: 10px"))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(task.progress))
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Message
        self.message_label = QLabel(task.message)
        self.message_label.setStyleSheet("color: #555; font-size: 12px; font-style: italic;")
        layout.addWidget(self.message_label)
        
    def update_task(self, task: GenerationTask) -> None:
        """Updates the widget with new task state."""
        self.status_label.setText(task.status.value)
        self.status_label.setStyleSheet(self._get_status_style(task.status))
        
        self.progress_bar.setValue(int(task.progress))
        self.message_label.setText(task.message)
        
    def _get_status_style(self, status: TaskStatus) -> str:
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
