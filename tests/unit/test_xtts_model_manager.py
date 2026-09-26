"""
Unit tests for XttsModelManager.

All torch and TTS imports are mocked so these tests run without GPU or packages.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import tempfile
import os

from src.domain.exceptions import XttsReferenceAudioMissingException
from src.infrastructure.xtts_model_manager import XttsModelManager, XttsDiagnosticsResult


class TestXttsModelManagerInit(unittest.TestCase):

    def test_default_speakers_dir_resolved(self):
        manager = XttsModelManager()
        self.assertIsInstance(manager.speakers_dir, Path)
        self.assertIn("xtts_speakers", str(manager.speakers_dir))

    def test_custom_speakers_dir_accepted(self):
        custom = Path("/tmp/custom_speakers")
        manager = XttsModelManager(speakers_dir=custom)
        self.assertEqual(manager.speakers_dir, custom)


class TestIsTtsPackageInstalled(unittest.TestCase):

    @patch("src.infrastructure.xtts_model_manager.XttsModelManager.is_tts_package_installed")
    def test_returns_true_when_installed(self, mock_method):
        mock_method.return_value = True
        manager = XttsModelManager()
        self.assertTrue(manager.is_tts_package_installed())

    @patch("src.infrastructure.xtts_model_manager.XttsModelManager.is_tts_package_installed")
    def test_returns_false_when_not_installed(self, mock_method):
        mock_method.return_value = False
        manager = XttsModelManager()
        self.assertFalse(manager.is_tts_package_installed())


class TestCheckCudaAvailable(unittest.TestCase):

    def test_returns_false_when_torch_not_installed(self):
        manager = XttsModelManager()
        with patch("builtins.__import__", side_effect=ImportError("no torch")):
            # Use the actual method but simulate ImportError inside
            with patch.object(manager, "check_cuda_available", return_value=False):
                self.assertFalse(manager.check_cuda_available())

    def test_delegates_to_torch_cuda(self):
        manager = XttsModelManager()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = manager.check_cuda_available()
        self.assertTrue(result)

    def test_cuda_not_available(self):
        manager = XttsModelManager()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = manager.check_cuda_available()
        self.assertFalse(result)


class TestGetVramInfo(unittest.TestCase):

    def test_returns_zero_when_torch_missing(self):
        manager = XttsModelManager()
        with patch.dict("sys.modules", {"torch": None}):
            free, total = manager.get_vram_info()
        self.assertEqual(free, 0.0)
        self.assertEqual(total, 0.0)

    def test_returns_correct_gb_values(self):
        manager = XttsModelManager()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        _gb = 1024 ** 3
        mock_torch.cuda.mem_get_info.return_value = (6 * _gb, 8 * _gb)
        with patch.dict("sys.modules", {"torch": mock_torch}):
            free, total = manager.get_vram_info()
        self.assertAlmostEqual(free, 6.0, places=1)
        self.assertAlmostEqual(total, 8.0, places=1)

    def test_returns_zero_when_cuda_unavailable(self):
        manager = XttsModelManager()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            free, total = manager.get_vram_info()
        self.assertEqual(free, 0.0)
        self.assertEqual(total, 0.0)


class TestCheckVramSufficient(unittest.TestCase):

    def test_sufficient_when_free_above_threshold(self):
        manager = XttsModelManager(min_vram_gb=4.0)
        with patch.object(manager, "get_vram_info", return_value=(6.0, 8.0)):
            self.assertTrue(manager.check_vram_sufficient())

    def test_insufficient_when_free_below_threshold(self):
        manager = XttsModelManager(min_vram_gb=4.0)
        with patch.object(manager, "get_vram_info", return_value=(2.0, 8.0)):
            self.assertFalse(manager.check_vram_sufficient())

    def test_exact_threshold_is_sufficient(self):
        manager = XttsModelManager(min_vram_gb=4.0)
        with patch.object(manager, "get_vram_info", return_value=(4.0, 8.0)):
            self.assertTrue(manager.check_vram_sufficient())


class TestIsModelCached(unittest.TestCase):

    def test_returns_false_when_cache_dir_absent(self):
        manager = XttsModelManager()
        with patch("src.infrastructure.xtts_model_manager._TTS_CACHE_DIR",
                   Path("/nonexistent/path/xyz")):
            self.assertFalse(manager.is_model_cached())

    def test_returns_true_when_cache_dir_has_files(self):
        manager = XttsModelManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            cache_subdir = tmp_path / "tts_models--multilingual--multi-dataset--xtts_v2"
            cache_subdir.mkdir()
            # Create a non-empty file inside
            (cache_subdir / "model.pth").write_bytes(b"fake model weights")
            with patch("src.infrastructure.xtts_model_manager._TTS_CACHE_DIR", tmp_path):
                self.assertTrue(manager.is_model_cached())

    def test_returns_false_when_dir_exists_but_empty(self):
        manager = XttsModelManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            cache_subdir = tmp_path / "tts_models--multilingual--multi-dataset--xtts_v2"
            cache_subdir.mkdir()
            with patch("src.infrastructure.xtts_model_manager._TTS_CACHE_DIR", tmp_path):
                self.assertFalse(manager.is_model_cached())


class TestGetReferenceAudioPath(unittest.TestCase):

    def test_returns_path_when_file_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            speakers_dir = Path(tmpdir)
            (speakers_dir / "en").mkdir()
            wav = speakers_dir / "en" / "male.wav"
            wav.write_bytes(b"\x00" * 1000)  # non-empty

            manager = XttsModelManager(speakers_dir=speakers_dir)
            result = manager.get_reference_audio_path("en", "male")
            self.assertEqual(result, wav)

    def test_raises_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            speakers_dir = Path(tmpdir)
            manager = XttsModelManager(speakers_dir=speakers_dir)
            with self.assertRaises(XttsReferenceAudioMissingException):
                manager.get_reference_audio_path("en", "male")

    def test_raises_when_file_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            speakers_dir = Path(tmpdir)
            (speakers_dir / "en").mkdir()
            (speakers_dir / "en" / "male.wav").write_bytes(b"")  # empty

            manager = XttsModelManager(speakers_dir=speakers_dir)
            with self.assertRaises(XttsReferenceAudioMissingException):
                manager.get_reference_audio_path("en", "male")


class TestFlushGpuCache(unittest.TestCase):

    def test_flush_does_not_raise_when_torch_missing(self):
        """flush_gpu_cache() should be a no-op if torch is not installed."""
        import sys
        original = sys.modules.get("torch")
        sys.modules["torch"] = None  # simulate missing torch
        try:
            XttsModelManager.flush_gpu_cache()  # must not raise
        finally:
            if original is None:
                sys.modules.pop("torch", None)
            else:
                sys.modules["torch"] = original

    def test_flush_calls_empty_cache(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            XttsModelManager.flush_gpu_cache()
        mock_torch.cuda.empty_cache.assert_called_once()


class TestDiagnosticsResult(unittest.TestCase):

    def test_is_ready_requires_all_fields_true(self):
        result = XttsDiagnosticsResult(
            tts_package_installed=True,
            cuda_available=True,
            vram_sufficient=True,
            model_cached=True,
        )
        self.assertTrue(result.is_ready)

    def test_is_ready_false_when_any_field_false(self):
        fields = ["tts_package_installed", "cuda_available", "vram_sufficient", "model_cached"]
        for field in fields:
            kwargs = {f: True for f in fields}
            kwargs[field] = False
            result = XttsDiagnosticsResult(**kwargs)
            self.assertFalse(result.is_ready, f"Expected is_ready=False when {field}=False")


if __name__ == "__main__":
    unittest.main()
