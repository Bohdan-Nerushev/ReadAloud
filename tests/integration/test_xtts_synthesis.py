"""
Integration tests for XTTS-v2 synthesis pipeline.

IMPORTANT: These tests require:
  - An NVIDIA GPU with >=4 GB free VRAM
  - TTS package installed: pip install -r xtts_requirements.txt
  - XTTS-v2 model downloaded (first run triggers auto-download)
  - Speaker reference WAV files in src/resource/xtts_speakers/

Run with:
    pytest tests/integration/test_xtts_synthesis.py -v -m requires_gpu

Skip automatically if prerequisites are not met.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import pytest


def _check_prerequisites() -> str:
    """Returns an empty string if all prerequisites are met, else a skip reason."""
    # Check TTS package
    import importlib.util
    if importlib.util.find_spec("TTS") is None:
        return "TTS package not installed (pip install -r xtts_requirements.txt)"

    # Check CUDA
    try:
        import torch
        if not torch.cuda.is_available():
            return "CUDA not available"
        free_bytes, _ = torch.cuda.mem_get_info(device=0)
        if free_bytes < 4 * 1024**3:
            return f"Insufficient VRAM: {free_bytes / 1024**3:.1f} GB free (need >=4 GB)"
    except Exception as exc:
        return f"CUDA check failed: {exc}"

    return ""


_SKIP_REASON = _check_prerequisites()


@pytest.mark.requires_gpu
@unittest.skipIf(bool(_SKIP_REASON), f"Skipping GPU tests: {_SKIP_REASON}")
class TestXttsSynthesisPipeline(unittest.TestCase):
    """End-to-end integration tests for XttsAudioGenerator."""

    @classmethod
    def setUpClass(cls):
        from src.infrastructure.xtts_model_manager import XttsModelManager
        from src.domain.xtts_audio_generator import XttsAudioGenerator

        cls.model_manager = XttsModelManager()
        cls.generator = XttsAudioGenerator(model_manager=cls.model_manager)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "generator"):
            cls.generator.close()

    def _get_speaker_wav(self, language: str = "en", gender: str = "male") -> Path:
        """Returns path to speaker WAV or skips test if missing."""
        try:
            return self.model_manager.get_reference_audio_path(language, gender)
        except Exception:
            self.skipTest(
                f"Speaker reference WAV for {language}/{gender} not found. "
                f"See src/resource/xtts_speakers/README.md"
            )

    def test_short_phrase_produces_mp3(self):
        """Synthesise a short English phrase and verify MP3 output."""
        self._get_speaker_wav("en", "male")
        chunk = __import__(
            "src.domain.models", fromlist=["AudioChunk"]
        ).AudioChunk(chunk_number=1, text_content="Hello, this is a test.")

        with tempfile.TemporaryDirectory() as tmpdir:
            results = self.generator.generate_audio_batch(
                [chunk], "en", "male", tmpdir
            )
        self.assertEqual(len(results), 1)
        path, duration = results[0]
        self.assertTrue(path.endswith(".mp3"), f"Expected .mp3, got: {path}")
        self.assertGreater(os.path.getsize(path), 0)
        self.assertGreater(duration, 0.0)

    def test_lazy_load_model_on_first_call(self):
        """Model should not be loaded until first synthesis call."""
        from src.infrastructure.xtts_model_manager import XttsModelManager
        from src.domain.xtts_audio_generator import XttsAudioGenerator

        fresh_gen = XttsAudioGenerator(model_manager=self.model_manager)
        self.assertFalse(fresh_gen.is_model_loaded)

        self._get_speaker_wav("en", "male")
        chunk = __import__(
            "src.domain.models", fromlist=["AudioChunk"]
        ).AudioChunk(chunk_number=1, text_content="Testing lazy load.")

        with tempfile.TemporaryDirectory() as tmpdir:
            fresh_gen.generate_audio_batch([chunk], "en", "male", tmpdir)

        self.assertTrue(fresh_gen.is_model_loaded)
        fresh_gen.close()

    def test_long_text_splits_into_segments(self):
        """A long text paragraph must produce exactly one MP3 (all segments combined)."""
        self._get_speaker_wav("en", "male")
        long_text = (
            "The quick brown fox jumps over the lazy dog. "
            "This sentence is used to demonstrate that long inputs are handled correctly. "
            "The XTTS engine will split this into multiple internal segments. "
            "But the output must be a single MP3 file per AudioChunk."
        )
        from src.domain.models import AudioChunk
        chunk = AudioChunk(chunk_number=1, text_content=long_text)

        with tempfile.TemporaryDirectory() as tmpdir:
            results = self.generator.generate_audio_batch([chunk], "en", "male", tmpdir)

        self.assertEqual(len(results), 1)
        path, duration = results[0]
        # Long text should produce at least 3 seconds of audio
        self.assertGreater(duration, 3.0)

    def test_unload_frees_model(self):
        """After unload_model(), is_model_loaded must be False."""
        # Ensure model is loaded first
        self._get_speaker_wav("en", "male")
        from src.domain.models import AudioChunk
        chunk = AudioChunk(chunk_number=1, text_content="Short test.")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.generator.generate_audio_batch([chunk], "en", "male", tmpdir)

        self.assertTrue(self.generator.is_model_loaded)
        self.generator.unload_model()
        self.assertFalse(self.generator.is_model_loaded)

    def test_protocol_compliance(self):
        """Generator must satisfy TtsGeneratorProtocol at runtime."""
        from src.domain.tts_generator_protocol import TtsGeneratorProtocol
        self.assertIsInstance(self.generator, TtsGeneratorProtocol)


@pytest.mark.requires_gpu
@unittest.skipIf(bool(_SKIP_REASON), f"Skipping GPU tests: {_SKIP_REASON}")
class TestXttsDiagnostics(unittest.TestCase):

    def test_run_diagnostics_returns_result(self):
        from src.infrastructure.xtts_model_manager import XttsModelManager, XttsDiagnosticsResult
        manager = XttsModelManager()
        result = manager.run_diagnostics()
        self.assertIsInstance(result, XttsDiagnosticsResult)
        # On a machine with TTS+CUDA this should be fully ready
        self.assertTrue(result.tts_package_installed)
        self.assertTrue(result.cuda_available)
        self.assertTrue(result.vram_sufficient)


if __name__ == "__main__":
    unittest.main()
