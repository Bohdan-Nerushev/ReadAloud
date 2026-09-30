"""
Regression tests for C1 / H7: XTTS failures must never become silent chunks,
and the model must be (re)loaded for every chunk after an OOM unload.
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.domain.exceptions import (
    FatalGenerationException,
    TransientGenerationException,
    XttsModelNotLoadedException,
    XttsNotAvailableException,
    XttsOutOfMemoryException,
)
from src.domain.models import AudioChunk
from src.domain.xtts_audio_generator import XttsAudioGenerator
from src.infrastructure.xtts_model_manager import XttsModelManager


def _gen():
    manager = MagicMock(spec=XttsModelManager)
    manager.get_reference_audio_path.return_value = Path("/tmp/en/male.wav")
    return XttsAudioGenerator(model_manager=manager)


def _chunks(n):
    return [AudioChunk(chunk_number=i, text_content=f"Sentence number {i}.") for i in range(1, n + 1)]


class _FakeLoader:
    """Replaces _ensure_model_loaded: installs a fresh mock model when missing."""

    def __init__(self, gen, tts_side_effects):
        self.gen = gen
        self.model = MagicMock()
        self.model.tts.side_effect = tts_side_effects
        self.loads = 0

    def __call__(self):
        if not self.gen._model_loaded:
            self.loads += 1
            self.gen._model = self.model
            self.gen._model_loaded = True


class TestSilenceOnlyForUnspeakableText(unittest.TestCase):

    def _loaded(self):
        g = _gen()
        g._model = MagicMock()
        g._model_loaded = True
        return g

    def test_symbols_only_returns_silence_without_calling_model(self):
        g = self._loaded()
        self.assertGreater(len(g._synthesise_segment("***", "en", "/s.wav")), 0)
        g._model.tts.assert_not_called()

    def test_empty_model_output_raises(self):
        g = self._loaded()
        g._model.tts.return_value = []
        with self.assertRaises(TransientGenerationException):
            g._synthesise_segment("Hello there.", "en", "/s.wav")

    def test_generic_exception_raises_transient(self):
        g = self._loaded()
        g._model.tts.side_effect = RuntimeError("phonemizer crashed")
        with self.assertRaises(TransientGenerationException) as ctx:
            g._synthesise_segment("Hello there.", "en", "/s.wav")
        self.assertIn("phonemizer crashed", str(ctx.exception))

    def test_attribute_error_is_not_swallowed(self):
        g = self._loaded()
        g._model.tts.side_effect = AttributeError("boom")
        with self.assertRaises(TransientGenerationException):
            g._synthesise_segment("Hello there.", "en", "/s.wav")

    def test_unloaded_model_raises_not_loaded(self):
        g = _gen()
        with self.assertRaises(XttsModelNotLoadedException):
            g._synthesise_segment("Hello there.", "en", "/s.wav")

    def test_oom_raises_oom_exception(self):
        g = self._loaded()
        g._model.tts.side_effect = RuntimeError("CUDA out of memory. Tried to allocate")
        with self.assertRaises(XttsOutOfMemoryException):
            g._synthesise_segment("Hello there.", "en", "/s.wav")


class TestBatchAfterOom(unittest.TestCase):
    """The original C1: OOM on chunk 1 turned chunks 2..n into silence."""

    def _run(self, gen, n, loader):
        out = tempfile.mkdtemp()
        with patch.object(gen, "_ensure_model_loaded", loader), \
                patch.object(gen, "_convert_wav_bytes_to_mp3", side_effect=lambda _b, path: Path(path).write_bytes(b"x")), \
                patch.object(gen, "_get_mp3_duration", return_value=1.0), \
                patch("time.sleep"):
            return gen.generate_audio_batch(_chunks(n), "en", "male", out)

    def test_oom_then_reload_synthesises_every_chunk_with_real_model(self):
        gen = _gen()
        loader = _FakeLoader(gen, [RuntimeError("CUDA out of memory"), [0.1] * 50, [0.1] * 50])
        results = self._run(gen, 3, loader)
        # chunk 1 is reported failed (None); 2 and 3 are really synthesised, not silence
        self.assertIsNone(results[0])
        self.assertIsNotNone(results[1])
        self.assertIsNotNone(results[2])
        self.assertEqual(loader.model.tts.call_count, 3)
        self.assertGreaterEqual(loader.loads, 2)

    def test_model_unloaded_between_chunks_is_reloaded(self):
        gen = _gen()
        loader = _FakeLoader(gen, [[0.1] * 50, [0.1] * 50])
        calls = {"n": 0}

        def tts(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                gen.unload_model()
            return [0.1] * 50

        loader.model.tts.side_effect = tts
        self._run(gen, 2, loader)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(loader.loads, 2)

    def test_persistent_failure_is_reported_not_silenced(self):
        gen = _gen()
        loader = _FakeLoader(gen, RuntimeError("phonemizer crashed"))
        results = self._run(gen, 2, loader)
        # Failed chunks are reported as None so the pipeline can retry them.
        self.assertEqual(results, [None, None])

    def test_reload_failure_fails_visibly(self):
        gen = _gen()

        def broken():
            raise XttsNotAvailableException("no CUDA")

        with patch.object(gen, "_ensure_model_loaded", broken):
            with self.assertRaises(XttsNotAvailableException):
                gen.generate_audio_batch(_chunks(2), "en", "male", tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()
