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


class TransientGenerationException(GenerationException):
    """
    Raised when audio generation fails due to a transient (recoverable) error.

    Transient errors are network timeouts, rate-limit responses, or temporary
    service unavailability. The retry mechanism SHOULD retry on this type.

    Examples:
        - Network connection reset
        - Edge TTS HTTP 429 Too Many Requests
        - asyncio.TimeoutError while awaiting TTS response
    """
    pass


class FatalGenerationException(GenerationException):
    """
    Raised when audio generation fails due to a fatal (non-recoverable) error.

    Fatal errors indicate that retrying will never succeed and should be reported
    immediately, skipping remaining retry attempts.

    Examples:
        - Invalid or empty text content
        - Unsupported language/voice configuration
        - Corrupted or unreadable input chunk
    """
    pass


class AssemblyException(ReadAloudException):
    """Raised during audio files assembly."""
    pass


class ConfigurationException(ReadAloudException):
    """Raised when project configuration is invalid."""
    pass

