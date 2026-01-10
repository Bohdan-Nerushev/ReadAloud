import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

def setup_logging(log_dir: str = "logs") -> None:
    """
    Configures the application logging system.
    
    Args:
        log_dir: Directory to store log files
    """
    try:
        log_path = Path(log_dir)
        log_path.mkdir(exist_ok=True, parents=True)
        
        log_file = log_path / "app.log"
        
        # Create formatters and handlers
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_formatter = logging.Formatter(
            '%(levelname)s: %(message)s'
        )
        
        # File Handler (Rotating)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding='utf-8'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)
        
        # Console Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(logging.INFO)
        
        # Root Logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        
        # Remove existing handlers to avoid duplicates
        if root_logger.hasHandlers():
            root_logger.handlers.clear()
            
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
        
        logging.info("Logging system initialized")
        
    except Exception as e:
        print(f"Failed to setup logging: {e}")
