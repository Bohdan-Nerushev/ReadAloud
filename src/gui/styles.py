"""
GUI styling constants.

This module provides consistent styling for the application's graphical interface.
"""


class Styles:
    """Constants for GUI styling."""
    
    BUTTON_START = """
        QPushButton {
            background-color: #4CAF50;
            color: white;
            border: none;
            padding: 10px 20px;
            font-size: 14px;
            font-weight: bold;
            border-radius: 5px;
        }
        QPushButton:hover {
            background-color: #45a049;
        }
        QPushButton:pressed {
            background-color: #3d8b40;
        }
        QPushButton:disabled {
            background-color: #cccccc;
            color: #666666;
        }
    """
    
    BUTTON_PAUSE = """
        QPushButton {
            background-color: #FF9800;
            color: white;
            border: none;
            padding: 10px 20px;
            font-size: 14px;
            font-weight: bold;
            border-radius: 5px;
        }
        QPushButton:hover {
            background-color: #e68900;
        }
        QPushButton:pressed {
            background-color: #cc7a00;
        }
    """
    
    BUTTON_STOP = """
        QPushButton {
            background-color: #f44336;
            color: white;
            border: none;
            padding: 10px 20px;
            font-size: 14px;
            font-weight: bold;
            border-radius: 5px;
        }
        QPushButton:hover {
            background-color: #da190b;
        }
        QPushButton:pressed {
            background-color: #c41606;
        }
    """
    
    BUTTON_FILE_SELECT = """
        QPushButton {
            background-color: #2196F3;
            color: white;
            border: none;
            padding: 8px 16px;
            font-size: 12px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #0b7dda;
        }
    """
    
    MAIN_WINDOW_STYLE = "background-color: #f5f5f5;"
    
    INPUT_STYLE = """
        QLineEdit, QComboBox, QSpinBox {
            background-color: white;
            color: #333333;
            border: 1px solid #cccccc;
            border-radius: 4px;
            padding: 5px;
            min-height: 30px;
            max-height: 30px;
            font-size: 13px;
        }
        QComboBox::drop-down {
            border: none;
            width: 20px;
        }
        QComboBox::down-arrow {
            image: none;
            border-left: 2px solid #555555;
            border-bottom: 2px solid #555555;
            width: 8px;
            height: 8px;
            transform: rotate(-45deg);
            margin-right: 5px;
        }
        QComboBox QAbstractItemView {
            background-color: white;
            color: #333333;
            selection-background-color: #6faaed;
            selection-color: white;
            border: 1px solid #cccccc;
        }
    """
    
    LABEL_TITLE = "font-size: 22px; font-weight: bold; color: #333333; margin-bottom: 10px;"
    LABEL_FIELD = "font-size: 13px; color: #555555; font-weight: bold;"
    LABEL_STATUS = "font-size: 15px; color: #333333; padding: 10px; font-weight: bold; min-height: 40px;"
    
    CARD_STYLE = """
        QWidget#ConfigCard {
            background-color: #ffffff;
            border-radius: 10px;
            border: 1px solid #e0e0e0;
        }
    """
    
    LABEL_FILE_DISPLAY = """
        color: #333333; 
        background-color: white; 
        border: 1px solid #cccccc; 
        border-radius: 4px; 
        padding: 5px;
        min-height: 20px;
        max-height: 20px;
    """
    
    PROGRESS_BAR_STYLE = """
        QProgressBar {
            background-color: white;
            border: 1px solid #ffffff;
            border-radius: 5px;
            text-align: center;
            color: #333333;
            font-weight: bold;
            min-height: 35px;
            max-height: 35px;
            font-size: 14px;
        }
        QProgressBar::chunk {
            background-color: #4CAF50;
            border-radius: 4px;
        }
    """
    
    WINDOW_MIN_WIDTH = 900
    WINDOW_MIN_HEIGHT = 900
    
    PROGRESS_WIDGET_MIN_HEIGHT = 120
    
    SPACING_SMALL = 5
    SPACING_MEDIUM = 12
    SPACING_LARGE = 20
    
    MARGIN = 30
