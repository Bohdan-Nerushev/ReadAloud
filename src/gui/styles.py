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
    
    LABEL_TITLE = "font-size: 16px; font-weight: bold; color: #333333;"
    LABEL_FIELD = "font-size: 12px; color: #666666;"
    LABEL_STATUS = "font-size: 13px; color: #333333; padding: 5px;"
    
    WINDOW_MIN_WIDTH = 600
    WINDOW_MIN_HEIGHT = 450
    
    SPACING_SMALL = 5
    SPACING_MEDIUM = 10
    SPACING_LARGE = 20
    
    MARGIN = 20
