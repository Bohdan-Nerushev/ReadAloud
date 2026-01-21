"""
Custom exceptions for the ReadAloud application.
"""

from typing import Optional


class ReadAloudException(Exception):
    """Base exception for all ReadAloud errors."""

    def __init__(
            self,
            message: str,
            cause: Optional[Exception] = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause


class PreparationException(ReadAloudException):
    """Raised during text processing or chunking."""


class GenerationException(ReadAloudException):
    """Raised during audio generation."""


class AssemblyException(ReadAloudException):
    """Raised during audio files assembly."""


class ConfigurationException(ReadAloudException):
    """Raised when project configuration is invalid."""
