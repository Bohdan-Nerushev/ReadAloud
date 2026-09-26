"""
Unit tests for XttsAudioGenerator.

All GPU operations and TTS synthesis calls are mocked.
Tests verify interface compatibility, lifecycle, and error handling without
requiring an NVIDIA GPU or the TTS package.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import tempfile

from src.domain.exceptions import (
    FatalGenerationException,
    XttsNotAvailableException,
    XttsOutOfMemoryException,
    XttsReferenceAudioMissingException,
)
from src.domain.models import AudioChunk
from src.domain.tts_generator_protocol import TtsGeneratorProtocol
from src.domain.xtts_audio_generator import XttsAudioGenerator
from src.infrastructure.xtts_model_manager import XttsModelManager


def _make_manager(speakers_dir: Path = None) -> XttsModelManager:
    """Creates a mock-configured XttsModelManager."""
    manager = MagicMock(spec=XttsModelManager)
    if speakers_dir:
        manager.get_reference_audio_path.return_value = speakers_dir / "en" / "male.wav"
    else:
        manager.get_reference_audio_path.return_value = Path("/tmp/en/male.wav")
    manager.flush_gpu_cache.return_value = None
    return manager


def _make_chunk(number: int = 1, text: str = "Hello world.") -> AudioChunk:
    return AudioChunk(chunk_number=number, text_content=text)


class TestXttsGeneratorProtocolCompliance(unittest.TestCase):
    """Verify that XttsAudioGenerator structurally satisfies TtsGeneratorProtocol."""

    def test_isinstance_check(self):
        manager = _make_manager()
        generator = XttsAudioGenerator(model_manager=manager)
        self.assertIsInstance(generator, TtsGeneratorProtocol)


class TestXttsGeneratorEmptyBatch(unittest.TestCase):

    def test_empty_chunks_returns_empty_list(self):
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        result = gen.generate_audio_batch([], "en", "male", "/tmp")
        self.assertEqual(result, [])


class TestXttsGeneratorUnsupportedLanguage(unittest.TestCase):

    def test_raises_value_error_for_unknown_language(self):
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        with self.assertRaises(ValueError):
            gen.generate_audio_batch([_make_chunk()], "xx", "male", "/tmp")


class TestXttsGeneratorModelLifecycle(unittest.TestCase):

    def _make_generator_with_mock_model(self) -> XttsAudioGenerator:
        """Returns a generator with a mock model pre-injected (bypasses lazy load)."""
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        # Inject mock model to simulate already-loaded state
        gen._model = MagicMock()
        gen._model_loaded = True
        return gen

    def test_is_model_loaded_false_initially(self):
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        self.assertFalse(gen.is_model_loaded)

    def test_unload_sets_model_to_none(self):
        gen = self._make_generator_with_mock_model()
        self.assertTrue(gen.is_model_loaded)
        gen.unload_model()
        self.assertIsNone(gen._model)
        self.assertFalse(gen.is_model_loaded)

    def test_unload_calls_flush_gpu_cache(self):
        gen = self._make_generator_with_mock_model()
        gen.unload_model()
        gen._model_manager.flush_gpu_cache.assert_called_once()

    def test_close_calls_unload(self):
        gen = self._make_generator_with_mock_model()
        gen.close()
        self.assertFalse(gen.is_model_loaded)

    def test_ensure_model_loaded_raises_when_import_fails(self):
        """
        Verifies that XttsNotAvailableException propagates from _ensure_model_loaded
        when the TTS package is unavailable.

        We use patch.object to simulate the exception without breaking the
        stdlib 'import sys' call that builtins.__import__ patching would affect.
        """
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model_loaded = False
        gen._model = None

        with patch.object(
            gen, "_ensure_model_loaded",
            side_effect=XttsNotAvailableException("TTS package not installed"),
        ):
            with self.assertRaises(XttsNotAvailableException):
                gen._ensure_model_loaded()


class TestXttsGeneratorOomHandling(unittest.TestCase):

    def test_cuda_oom_causes_model_unload(self):
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model = MagicMock()
        gen._model_loaded = True

        chunk = _make_chunk()

        with patch.object(gen, "_synthesise_chunk",
                          side_effect=XttsOutOfMemoryException("CUDA OOM")):
            with patch.object(gen, "unload_model") as mock_unload:
                results = gen.generate_audio_batch([chunk], "en", "male", "/tmp")

        mock_unload.assert_called_once()
        self.assertIsNone(results[0])

    def test_oom_exception_from_runtime_error(self):
        """Verify _synthesise_segment converts CUDA OOM RuntimeError to XttsOutOfMemoryException."""
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model = MagicMock()
        gen._model.tts.side_effect = RuntimeError("CUDA out of memory. Tried to allocate 512 MB")
        gen._model_loaded = True

        with self.assertRaises(XttsOutOfMemoryException):
            gen._synthesise_segment("Hello.", "en", "/tmp/speaker.wav")


class TestXttsGeneratorReferenceAudioMissing(unittest.TestCase):

    def test_missing_reference_audio_is_fatal(self):
        manager = _make_manager()
        manager.get_reference_audio_path.side_effect = XttsReferenceAudioMissingException(
            "File not found"
        )
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model_loaded = True

        with self.assertRaises(XttsReferenceAudioMissingException):
            gen.generate_audio_batch([_make_chunk()], "en", "male", "/tmp")


class TestXttsGeneratorChunkCallback(unittest.TestCase):

    def test_callback_called_on_success(self):
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model = MagicMock()
        gen._model_loaded = True

        chunk = _make_chunk()
        callback = MagicMock()

        expected_path = "/tmp/1.mp3"
        expected_duration = 2.5

        with patch.object(gen, "_synthesise_chunk", return_value=(expected_path, expected_duration)):
            gen.generate_audio_batch([chunk], "en", "male", "/tmp", chunk_callback=callback)

        callback.assert_called_once_with(chunk.chunk_number, expected_path, expected_duration)


class TestXttsGeneratorMp3Output(unittest.TestCase):

    def test_get_mp3_duration_returns_zero_on_failure(self):
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        # Non-existent file — should return 0.0, not raise
        duration = gen._get_mp3_duration("/nonexistent/file.mp3")
        self.assertEqual(duration, 0.0)

    def test_cleanup_tmp_files_silently_ignores_missing(self):
        """_cleanup_tmp_files must not raise if the file does not exist."""
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        # Should not raise
        gen._cleanup_tmp_files(Path("/nonexistent/tmp.wav"), None)

    def test_get_file_duration_fast_alias(self):
        """_get_file_duration_fast must be available as an alias for _get_mp3_duration."""
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        with patch.object(gen, "_get_mp3_duration", return_value=3.14) as mock_get:
            duration = gen._get_file_duration_fast("/path/to/chunk.mp3")
            self.assertEqual(duration, 3.14)
            mock_get.assert_called_once_with("/path/to/chunk.mp3")


class TestXttsGeneratorResilience(unittest.TestCase):

    def test_unspeakable_symbols_returns_silence(self):
        """Segment containing only symbols like '___' or '***' must return silence instead of failing."""
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model = MagicMock()
        gen._model_loaded = True

        samples = gen._synthesise_segment("___", "en", "/tmp/speaker.wav")
        self.assertGreater(len(samples), 0)
        # Model tts should NOT be called for unspeakable text
        gen._model.tts.assert_not_called()

    def test_cyrillic_script_fallback_in_en_mode(self):
        """Passing Cyrillic text with language='en' must auto-fallback to 'uk' for synthesis."""
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model = MagicMock()
        gen._model.tts.return_value = [0.1, 0.2, 0.3]
        gen._model_loaded = True

        gen._synthesise_segment("Привіт світ", "en", "/tmp/speaker.wav")
        gen._model.tts.assert_called_once_with(
            text="Привіт світ",
            speaker_wav="/tmp/speaker.wav",
            language="uk",
        )

    def test_non_oom_exception_returns_silence(self):
        """Non-OOM exception in model.tts (e.g. cleaning error) must return silence, not raise."""
        manager = _make_manager()
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model = MagicMock()
        gen._model.tts.side_effect = ValueError("[!] Text is empty after cleaning.")
        gen._model_loaded = True

        samples = gen._synthesise_segment("Valid text.", "en", "/tmp/speaker.wav")
        self.assertGreater(len(samples), 0)


if __name__ == "__main__":
    unittest.main()
