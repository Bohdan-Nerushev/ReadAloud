"""
GUI styling constants.

This module provides consistent styling for the application's graphical interface.
"""

from typing import Dict

class Palette:
    """Color palette for the application."""
    
    # Base Colors
    PRIMARY = "#4CAF50"      # Green
    PRIMARY_HOVER = "#45a049"
    PRIMARY_PRESSED = "#3d8b40"
    
    SECONDARY = "#2196F3"    # Blue
    SECONDARY_HOVER = "#0b7dda"
    
    WARNING = "#FF9800"      # Orange
    WARNING_HOVER = "#e68900"
    WARNING_PRESSED = "#cc7a00"
    
    ERROR = "#f44336"        # Red
    ERROR_HOVER = "#da190b"
    ERROR_PRESSED = "#c41606"
    
    DISABLED_BG = "#cccccc"
    DISABLED_TEXT = "#666666"
    
    # Backgrounds
    BG_MAIN = "#f5f5f5"
    BG_CARD = "#ffffff"
    BG_INPUT = "#ffffff"
    
    # Text
    TEXT_PRIMARY = "#333333"
    TEXT_SECONDARY = "#555555"
    TEXT_INVERSE = "#ffffff"
    
    # Borders
    BORDER_DEFAULT = "#cccccc"
    BORDER_LIGHT = "#e0e0e0"
    BORDER_FOCUS = "#6faaed"


class Styles:
    """Constants for GUI styling."""
    
    BUTTON_START = f"""
        QPushButton {{
            background-color: {Palette.PRIMARY};
            color: {Palette.TEXT_INVERSE};
            border: none;
            padding: 10px 20px;
            font-size: 14px;
            font-weight: bold;
            border-radius: 5px;
        }}
        QPushButton:hover {{
            background-color: {Palette.PRIMARY_HOVER};
        }}
        QPushButton:pressed {{
            background-color: {Palette.PRIMARY_PRESSED};
        }}
        QPushButton:disabled {{
            background-color: {Palette.DISABLED_BG};
            color: {Palette.DISABLED_TEXT};
        }}
    """
    
    BUTTON_PAUSE = f"""
        QPushButton {{
            background-color: {Palette.WARNING};
            color: {Palette.TEXT_INVERSE};
            border: none;
            padding: 10px 20px;
            font-size: 14px;
            font-weight: bold;
            border-radius: 5px;
        }}
        QPushButton:hover {{
            background-color: {Palette.WARNING_HOVER};
        }}
        QPushButton:pressed {{
            background-color: {Palette.WARNING_PRESSED};
        }}
    """
    
    BUTTON_STOP = f"""
        QPushButton {{
            background-color: {Palette.ERROR};
            color: {Palette.TEXT_INVERSE};
            border: none;
            padding: 10px 20px;
            font-size: 14px;
            font-weight: bold;
            border-radius: 5px;
        }}
        QPushButton:hover {{
            background-color: {Palette.ERROR_HOVER};
        }}
        QPushButton:pressed {{
            background-color: {Palette.ERROR_PRESSED};
        }}
    """
    
    BUTTON_FILE_SELECT = f"""
        QPushButton {{
            background-color: {Palette.SECONDARY};
            color: {Palette.TEXT_INVERSE};
            border: none;
            padding: 8px 16px;
            font-size: 12px;
            border-radius: 4px;
        }}
        QPushButton:hover {{
            background-color: {Palette.SECONDARY_HOVER};
        }}
    """
    
    MAIN_WINDOW_STYLE = f"background-color: {Palette.BG_MAIN};"
    
    INPUT_STYLE = f"""
        QLineEdit, QComboBox, QSpinBox {{
            background-color: {Palette.BG_INPUT};
            color: {Palette.TEXT_PRIMARY};
            border: 1px solid {Palette.BORDER_DEFAULT};
            border-radius: 4px;
            padding: 5px;
            height: 30px;
            font-size: 13px;
        }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QComboBox::down-arrow {{
            image: none;
            border-left: 2px solid {Palette.TEXT_SECONDARY};
            border-bottom: 2px solid {Palette.TEXT_SECONDARY};
            width: 8px;
            height: 8px;
            margin-right: 5px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {Palette.BG_INPUT};
            color: {Palette.TEXT_PRIMARY};
            selection-background-color: {Palette.BORDER_FOCUS};
            selection-color: {Palette.TEXT_INVERSE};
            border: 1px solid {Palette.BORDER_DEFAULT};
        }}
    """
    
    LABEL_TITLE = f"font-size: 22px; font-weight: bold; color: {Palette.TEXT_PRIMARY}; margin-bottom: 10px;"
    LABEL_FIELD = f"font-size: 13px; color: {Palette.TEXT_SECONDARY}; font-weight: bold;"
    LABEL_STATUS = f"font-size: 15px; color: {Palette.TEXT_PRIMARY}; padding: 10px; font-weight: bold; min-height: 40px;"
    
    CARD_STYLE = f"""
        QWidget#ConfigCard {{
            background-color: {Palette.BG_CARD};
            border-radius: 10px;
            border: 1px solid {Palette.BORDER_LIGHT};
        }}
    """
    
    LABEL_FILE_DISPLAY = f"""
        color: {Palette.TEXT_PRIMARY}; 
        background-color: {Palette.BG_INPUT}; 
        border: 1px solid {Palette.BORDER_DEFAULT}; 
        border-radius: 4px; 
        padding: 5px;
        height: 20px;
    """
    
    PROGRESS_BAR_STYLE = f"""
        QProgressBar {{
            background-color: {Palette.BG_INPUT};
            border: 1px solid {Palette.TEXT_INVERSE};
            border-radius: 5px;
            text-align: center;
            color: {Palette.TEXT_PRIMARY};
            font-weight: bold;
            height: 35px;
            font-size: 14px;
        }}
        QProgressBar::chunk {{
            background-color: {Palette.PRIMARY};
            border-radius: 4px;
        }}
    """
    
    WINDOW_WIDTH = 1100
    WINDOW_HEIGHT = 850
    WINDOW_MIN_WIDTH = 900
    WINDOW_MIN_HEIGHT = 650
    
    QUEUE_LIST_HEIGHT = 350
    QUEUE_ITEM_HEIGHT = 120
    PROGRESS_BAR_HEIGHT = 35
    QUEUE_ITEM_PROGRESS_HEIGHT = 20
    INPUT_FIELD_HEIGHT = 30
    
    PROGRESS_WIDGET_HEIGHT = 120
    PROGRESS_WIDGET_MIN_HEIGHT = 120
    
    SPACING_SMALL = 5
    SPACING_MEDIUM = 12
    SPACING_LARGE = 20
    
    MARGIN = 30
