"""
Custom exceptions for the ReadAloud application.
"""

from typing import Optional


class ReadAloudException(Exception):
    """
    Base exception for all ReadAloud errors.
    
    Treat as an unchecked exception in the application logic.
    """

    def __init__(
            self,
            message: str,
            cause: Optional[Exception] = None
    ) -> None:
        """
        Initialize the exception.
        
        Args:
            message: Error message
            cause: Optional underlying exception
        """
        super().__init__(message)
        self.message = message
        self.cause = cause


class PreparationException(ReadAloudException):
    """Raised during text processing or chunking."""
    pass


class GenerationException(ReadAloudException):
    """Raised during audio generation."""
    pass


class AssemblyException(ReadAloudException):
    """Raised during audio files assembly."""
    pass


class ConfigurationException(ReadAloudException):
    """Raised when project configuration is invalid."""
    pass

