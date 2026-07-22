"""
Unit tests for retry classification and error handling in AudioGenerator.
"""

import unittest
import asyncio
from pathlib import Path
from src.domain.audio_generator import _is_transient_error, AudioGenerator
from src.domain.exceptions import TransientGenerationException, FatalGenerationException
from src.domain.models import AudioChunk


class DummyHTTPError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class TestRetryClassification(unittest.TestCase):

    def test_fatal_exception_types(self):
        """Verify that standard programming/validation errors are classified as fatal."""
        self.assertFalse(_is_transient_error(ValueError("Invalid argument")))
        self.assertFalse(_is_transient_error(TypeError("Type error")))
        self.assertFalse(_is_transient_error(AttributeError("Attribute missing")))
        self.assertFalse(_is_transient_error(KeyError("key")))
        self.assertFalse(_is_transient_error(IndexError("index")))
        self.assertFalse(_is_transient_error(FileNotFoundError("File not found")))
        self.assertFalse(_is_transient_error(PermissionError("Access denied")))
        self.assertFalse(_is_transient_error(FatalGenerationException("Fatal error")))

    def test_transient_exception_types(self):
        """Verify that recoverable network/timeout errors are classified as transient."""
        self.assertTrue(_is_transient_error(TransientGenerationException("Network timeout")))
        self.assertTrue(_is_transient_error(asyncio.TimeoutError("Timed out")))
        self.assertTrue(_is_transient_error(ConnectionResetError("Connection reset")))
        self.assertTrue(_is_transient_error(TimeoutError("Operation timed out")))

    def test_http_status_codes(self):
        """Verify that 400, 401, 403 are fatal while 429, 503 are transient."""
        self.assertFalse(_is_transient_error(DummyHTTPError("Bad Request", status=400)))
        self.assertFalse(_is_transient_error(DummyHTTPError("Unauthorized", status=401)))
        self.assertFalse(_is_transient_error(DummyHTTPError("Forbidden", status=403)))
        
        self.assertTrue(_is_transient_error(DummyHTTPError("Too Many Requests", status=429)))
        self.assertTrue(_is_transient_error(DummyHTTPError("Service Unavailable", status=503)))

    def test_empty_text_raises_fatal(self):
        """Verify that AudioGenerator rejects empty or whitespace-only text immediately."""
        generator = AudioGenerator()
        try:
            chunk = AudioChunk(chunk_number=1, text_content="   ")
            loop = generator._loop
            fut = asyncio.run_coroutine_threadsafe(
                generator._generate_one_with_retry(
                    chunk, "en-US-GuyNeural", Path("/tmp")
                ),
                loop
            )
            with self.assertRaises(Exception) as cm:
                fut.result(timeout=5.0)
            self.assertIn("empty", str(cm.exception).lower())
        finally:
            generator.close()


if __name__ == "__main__":
    unittest.main()
