"""
Queue list widget.

This module defines the widget used to display the list of all tasks.
"""

from typing import Dict
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QListWidget, QListWidgetItem, QLabel
from PyQt6.QtCore import Qt
from src.domain.models import GenerationTask
from src.gui.widgets.queue_item import QueueItemWidget
from src.gui.styles import Styles, Palette

class QueueListWidget(QWidget):
    """
    Widget displaying the queue of tasks.
    """
    
    def __init__(
            self,
            parent: QWidget = None
    ) -> None:
        """Initialize the QueueListWidget."""
        super().__init__(parent)
        self._items: Dict[str, QueueItemWidget] = {}
        self._setup_ui()
        
    def _setup_ui(self) -> None:
        """Sets up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Title
        title = QLabel("Task Queue")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {Palette.TEXT_PRIMARY}; margin-bottom: 5px;")
        layout.addWidget(title)
        
        # List
        self._list_widget = QListWidget()
        self._list_widget.setStyleSheet(f"""
            QListWidget {{
                background-color: {Palette.BG_MAIN};
                border: 1px solid {Palette.BORDER_DEFAULT};
                border-radius: 5px;
            }}
            QListWidget::item {{
                border-bottom: 1px solid {Palette.BORDER_LIGHT};
                padding: 5px;
            }}
        """)
        self._list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list_widget.setSpacing(5)
        layout.addWidget(self._list_widget)
        
    def add_task(self, task: GenerationTask) -> None:
        """
        Adds a task to the list.
        """
        item = QListWidgetItem(self._list_widget)
        widget = QueueItemWidget(task)
        
        item.setSizeHint(widget.sizeHint())
        
        self._list_widget.addItem(item)
        self._list_widget.setItemWidget(item, widget)
        
        self._items[task.id] = widget
        
        # Scroll to bottom
        self._list_widget.scrollToBottom()
        
    def update_task(self, task: GenerationTask) -> None:
        """
        Updates an existing task in the list.
        """
        if task.id in self._items:
            self._items[task.id].update_task(task)
