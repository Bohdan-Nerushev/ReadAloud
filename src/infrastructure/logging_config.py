import logging
import sys
import threading
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Any, Optional

# Thread-local storage for Correlation ID
_context = threading.local()

def set_correlation_id(correlation_id: str) -> None:
    """Sets the correlation ID for the current thread."""
    _context.correlation_id = correlation_id

def get_correlation_id() -> str:
    """Gets the correlation ID for the current thread."""
    return getattr(_context, 'correlation_id', 'N/A')

class CorrelationIdFilter(logging.Filter):
    """Filter that adds correlation_id to log records."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True

class PiiMaskingFilter(logging.Filter):
    """Filter that masks sensitive path data in log messages."""
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            # Mask home directory
            home = str(Path.home())
            if home in record.msg:
                record.msg = record.msg.replace(home, "~")
        return True

def setup_logging(log_dir: str = "logs") -> None:
    """
    Configures the application logging system with Correlation IDs and masking.
    """
    try:
        log_path = Path(log_dir)
        log_path.mkdir(exist_ok=True, parents=True)
        
        log_file = log_path / "app.log"
        
        # Determine log level from environment or default to INFO
        # This allows changing level without code changes via export LOG_LEVEL=DEBUG
        env_level = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, env_level, logging.INFO)
        
        # Structured machine-readable format with Correlation ID
        file_formatter = logging.Formatter(
            '%(asctime)s [%(correlation_id)s] [%(levelname)s] %(name)s: %(message)s'
        )
        console_formatter = logging.Formatter(
            '[%(correlation_id)s] %(levelname)s: %(message)s'
        )
        
        # File Handler (Rotating)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(logging.DEBUG)  # File always gets DEBUG
        
        # Console Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(level)
        
        # Root Logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        
        # Add Filters
        corr_filter = CorrelationIdFilter()
        pii_filter = PiiMaskingFilter()
        
        # Remove existing handlers to avoid duplicates
        if root_logger.hasHandlers():
            root_logger.handlers.clear()
            
        # Add filters to handlers to ensure they are available for formatting
        file_handler.addFilter(corr_filter)
        file_handler.addFilter(pii_filter)
        console_handler.addFilter(corr_filter)
        console_handler.addFilter(pii_filter)
            
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

        
        logging.info("Logging system initialized with Correlation ID support")
        
    except Exception as e:
        print(f"Failed to setup logging: {e}")

