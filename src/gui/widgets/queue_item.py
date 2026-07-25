"""
Queue item widget.

This module defines the widget used to represent a single task in the queue list.
"""

from typing import Any
from pathlib import Path
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal
from src.domain.models import GenerationTask, TaskStatus
from src.gui.styles import Styles, Palette

class QueueItemWidget(QWidget):
    """
    Widget representing a single task in the queue.
    """
    
    deleteRequested = pyqtSignal(str)
    pauseRequested = pyqtSignal(str)
    
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

    def sizeHint(self):
        """Returns size hint matching Styles.QUEUE_ITEM_HEIGHT."""
        from PyQt6.QtCore import QSize
        return QSize(600, 165)

    def _get_file_size_str(self, file_path: Any) -> str:
        """Returns formatted string of file size."""
        if not isinstance(file_path, (str, Path)):
            return "N/A"
        try:
            p = Path(file_path).expanduser()
            if p.exists() and p.is_file():
                size_bytes = p.stat().st_size
                if size_bytes < 1024:
                    return f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    return f"{size_bytes / 1024:.1f} KB"
                else:
                    return f"{size_bytes / (1024 * 1024):.1f} MB"
        except Exception:
            pass
        return "N/A"

    def _get_est_duration_str(self, file_path: Any, speed: Any) -> str:
        """Returns estimated speech duration string based on file character count."""
        if not isinstance(file_path, (str, Path)):
            return "N/A"
        try:
            p = Path(file_path).expanduser()
            if p.exists() and p.is_file():
                char_count = 0
                if p.suffix.lower() == '.txt':
                    char_count = len(p.read_text(encoding='utf-8', errors='ignore'))
                else:
                    char_count = int(p.stat().st_size * 0.75)
                
                if char_count > 0:
                    try:
                        eff_speed = float(speed) if speed and float(speed) > 0 else 1.0
                    except (ValueError, TypeError):
                        eff_speed = 1.0
                    est_seconds = int(char_count / (15.0 * eff_speed))
                    
                    hours = est_seconds // 3600
                    minutes = (est_seconds % 3600) // 60
                    seconds = est_seconds % 60
                    
                    if hours > 0:
                        return f"~{hours}h {minutes}m"
                    elif minutes > 0:
                        return f"~{minutes}m {seconds}s"
                    else:
                        return f"~{seconds}s"
        except Exception:
            pass
        return "N/A"
        
    def _setup_ui(
            self,
            task: GenerationTask
    ) -> None:
        """Sets up the UI components."""
        # Main Layout (Vertical)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(4)
        
        # Row 1: Project Name | Status | Percentage
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        proj_name = str(getattr(task.config, 'project_name', 'Task')) if getattr(task, 'config', None) else 'Task'
        self.name_label = QLabel(proj_name)
        self.name_label.setStyleSheet(
            f"font-weight: bold; font-size: 14px; color: {Palette.TEXT_PRIMARY};"
        )
        header_layout.addWidget(self.name_label)
        
        header_layout.addStretch()
        
        status_obj = getattr(task, 'status', TaskStatus.PENDING)
        status_val = str(getattr(status_obj, 'value', status_obj))
        self.status_label = QLabel(status_val)
        self.status_label.setStyleSheet(self._get_status_style(status_obj))
        header_layout.addWidget(self.status_label)
        
        raw_progress = getattr(task, 'progress', 0.0)
        try:
            progress_pct = int(raw_progress) if isinstance(raw_progress, (int, float)) else 0
        except Exception:
            progress_pct = 0
        self.percentage_label = QLabel(f"{progress_pct}%")
        self.percentage_label.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {Palette.PRIMARY};"
        )
        header_layout.addWidget(self.percentage_label)
        layout.addLayout(header_layout)
        
        # Row 2: Input File & Output Paths
        paths_layout = QHBoxLayout()
        paths_layout.setSpacing(12)
        
        input_file = getattr(task.config, 'input_file_path', 'N/A') or 'N/A'
        output_dir = getattr(task.config, 'output_dir_path', 'N/A') or 'N/A'
        
        self.input_path_label = QLabel(f"File: {input_file}")
        self.input_path_label.setStyleSheet("color: #444; font-size: 11px;")
        paths_layout.addWidget(self.input_path_label, stretch=1)
        
        self.output_path_label = QLabel(f"Out: {output_dir}")
        self.output_path_label.setStyleSheet("color: #666; font-size: 11px;")
        paths_layout.addWidget(self.output_path_label, stretch=1)
        
        layout.addLayout(paths_layout)
        
        # Row 3: File Metadata & Task Settings
        settings_layout = QHBoxLayout()
        settings_layout.setSpacing(12)
        
        size_str = self._get_file_size_str(input_file)
        est_dur_str = self._get_est_duration_str(input_file, task.config.speed)
        
        lang_str = str(self._get_language_name(getattr(task.config, 'language', 'en')))
        raw_gender = getattr(task.config, 'gender', 'male')
        gender_str = str(raw_gender).capitalize() if isinstance(raw_gender, str) else "Male"
        speed_str = str(getattr(task.config, 'speed', 1.0))
        threads_str = str(getattr(task.config, 'thread_count', 1))

        for text in [
            f"Size: {size_str}",
            f"Est. Audio: {est_dur_str}",
            f"Lang: {lang_str}",
            f"Gender: {gender_str}",
            f"Speed: {speed_str}x",
            f"Threads: {threads_str}"
        ]:
            l = QLabel(text)
            l.setStyleSheet("color: #555; font-size: 11px; font-weight: 500;")
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
        
        self.pause_button = QPushButton("Pause")
        self.pause_button.setFixedSize(70, 24)
        self.pause_button.setStyleSheet(Styles.BUTTON_PAUSE + "QPushButton { padding: 0px; font-size: 11px; }")
        self.pause_button.clicked.connect(lambda: self.pauseRequested.emit(str(self.task_id)))
        buttons_group.addWidget(self.pause_button)
        
        self.delete_button = QPushButton("X")
        self.delete_button.setFixedSize(30, 24)
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
        self.delete_button.setEnabled(True)
        self.delete_button.show()
        
        if task.status in (TaskStatus.PROCESSING, TaskStatus.PAUSED, TaskStatus.PENDING):
            if task.status == TaskStatus.PAUSED:
                self.pause_button.setText("Resume")
            else:
                self.pause_button.setText("Pause")
            self.pause_button.setEnabled(True)
            self.pause_button.show()
        else:
            self.pause_button.hide()
        
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
