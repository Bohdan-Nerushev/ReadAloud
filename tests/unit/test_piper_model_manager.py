"""
Unit tests for PiperModelManager.

Tests filesystem-based model presence checks without any Docker dependency.
"""

from pathlib import Path

import pytest

from src.domain.exceptions import PiperModelMissingException
from src.infrastructure.piper_model_manager import PiperModelManager


VOICE = "ru_RU-dmitri-medium"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def models_dir(tmp_path: Path) -> Path:
    d = tmp_path / "piper_models"
    d.mkdir()
    return d


@pytest.fixture
def manager(models_dir: Path) -> PiperModelManager:
    return PiperModelManager(models_dir=models_dir)


def write_model_files(models_dir: Path, voice: str, onnx_size: int = 1024, json_size: int = 512) -> None:
    """Creates both model files with the given sizes."""
    (models_dir / f"{voice}.onnx").write_bytes(b"x" * onnx_size)
    (models_dir / f"{voice}.onnx.json").write_bytes(b"x" * json_size)


# ---------------------------------------------------------------------------
# Model presence checks
# ---------------------------------------------------------------------------

class TestIsModelPresent:
    def test_returns_true_when_both_files_exist(self, manager: PiperModelManager, models_dir: Path):
        write_model_files(models_dir, VOICE)
        assert manager.is_model_present(VOICE) is True

    def test_returns_false_when_onnx_missing(self, manager: PiperModelManager, models_dir: Path):
        (models_dir / f"{VOICE}.onnx.json").write_bytes(b"x" * 512)
        assert manager.is_model_present(VOICE) is False

    def test_returns_false_when_json_missing(self, manager: PiperModelManager, models_dir: Path):
        (models_dir / f"{VOICE}.onnx").write_bytes(b"x" * 1024)
        assert manager.is_model_present(VOICE) is False

    def test_returns_false_when_both_missing(self, manager: PiperModelManager, models_dir: Path):
        assert manager.is_model_present(VOICE) is False

    def test_returns_false_when_onnx_is_zero_bytes(self, manager: PiperModelManager, models_dir: Path):
        """A 0-byte file is treated as absent (incomplete download)."""
        (models_dir / f"{VOICE}.onnx").write_bytes(b"")
        (models_dir / f"{VOICE}.onnx.json").write_bytes(b"x" * 512)
        assert manager.is_model_present(VOICE) is False

    def test_returns_false_when_json_is_zero_bytes(self, manager: PiperModelManager, models_dir: Path):
        (models_dir / f"{VOICE}.onnx").write_bytes(b"x" * 1024)
        (models_dir / f"{VOICE}.onnx.json").write_bytes(b"")
        assert manager.is_model_present(VOICE) is False


# ---------------------------------------------------------------------------
# Missing files list
# ---------------------------------------------------------------------------

class TestGetMissingModelFiles:
    def test_empty_when_both_present(self, manager: PiperModelManager, models_dir: Path):
        write_model_files(models_dir, VOICE)
        assert manager.get_missing_model_files(VOICE) == []

    def test_lists_onnx_when_missing(self, manager: PiperModelManager, models_dir: Path):
        (models_dir / f"{VOICE}.onnx.json").write_bytes(b"x" * 512)
        missing = manager.get_missing_model_files(VOICE)
        assert len(missing) == 1
        assert ".onnx" in missing[0]
        assert ".onnx.json" not in missing[0]

    def test_lists_json_when_missing(self, manager: PiperModelManager, models_dir: Path):
        (models_dir / f"{VOICE}.onnx").write_bytes(b"x" * 1024)
        missing = manager.get_missing_model_files(VOICE)
        assert len(missing) == 1
        assert ".onnx.json" in missing[0]

    def test_lists_both_when_both_missing(self, manager: PiperModelManager, models_dir: Path):
        missing = manager.get_missing_model_files(VOICE)
        assert len(missing) == 2

    def test_lists_zero_byte_file_as_missing(self, manager: PiperModelManager, models_dir: Path):
        (models_dir / f"{VOICE}.onnx").write_bytes(b"")
        (models_dir / f"{VOICE}.onnx.json").write_bytes(b"x" * 512)
        missing = manager.get_missing_model_files(VOICE)
        assert len(missing) == 1
        assert ".onnx" in missing[0]


# ---------------------------------------------------------------------------
# validate_model_or_raise
# ---------------------------------------------------------------------------

class TestValidateModelOrRaise:
    def test_does_not_raise_when_model_present(self, manager: PiperModelManager, models_dir: Path):
        write_model_files(models_dir, VOICE)
        manager.validate_model_or_raise(VOICE)  # Should not raise

    def test_raises_when_model_missing(self, manager: PiperModelManager, models_dir: Path):
        with pytest.raises(PiperModelMissingException) as exc_info:
            manager.validate_model_or_raise(VOICE)
        assert VOICE in str(exc_info.value)
        assert ".onnx" in str(exc_info.value)

    def test_exception_message_lists_missing_files(self, manager: PiperModelManager, models_dir: Path):
        """The exception message should name every missing file."""
        with pytest.raises(PiperModelMissingException) as exc_info:
            manager.validate_model_or_raise(VOICE)
        msg = str(exc_info.value)
        assert ".onnx" in msg
        assert ".onnx.json" in msg


# ---------------------------------------------------------------------------
# Directory management
# ---------------------------------------------------------------------------

class TestModelsDirectory:
    def test_creates_dir_if_absent(self, tmp_path: Path):
        new_dir = tmp_path / "nonexistent" / "models"
        assert not new_dir.exists()
        manager = PiperModelManager(models_dir=new_dir)
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_does_not_fail_if_dir_already_exists(self, models_dir: Path):
        PiperModelManager(models_dir=models_dir)  # Should not raise
        PiperModelManager(models_dir=models_dir)  # Called twice — still fine


# ---------------------------------------------------------------------------
# Voice resolution
# ---------------------------------------------------------------------------

class TestVoiceResolution:
    def test_get_voice_for_russian_male(self, manager: PiperModelManager):
        voice = manager.get_voice_for_language("ru", "male")
        assert voice == "ru_RU-dmitri-medium"

    def test_raises_for_unsupported_language(self, manager: PiperModelManager):
        with pytest.raises(ValueError, match="not supported"):
            manager.get_voice_for_language("zz", "male")

    def test_is_language_supported_true(self, manager: PiperModelManager):
        assert manager.is_language_supported("ru") is True

    def test_is_language_supported_false(self, manager: PiperModelManager):
        assert manager.is_language_supported("zz") is False


# ---------------------------------------------------------------------------
# Size estimation
# ---------------------------------------------------------------------------

class TestSizeEstimation:
    def test_medium_voice_estimate(self, manager: PiperModelManager):
        size = manager.estimate_model_size_bytes("ru_RU-dmitri-medium")
        assert size == 65 * 1024 * 1024

    def test_low_voice_estimate(self, manager: PiperModelManager):
        size = manager.estimate_model_size_bytes("en_US-some-low")
        assert size == 40 * 1024 * 1024

    def test_x_low_voice_estimate(self, manager: PiperModelManager):
        size = manager.estimate_model_size_bytes("uk_UA-lada-x_low")
        assert size == 25 * 1024 * 1024

    def test_unknown_quality_falls_back_to_medium(self, manager: PiperModelManager):
        size = manager.estimate_model_size_bytes("ru_RU-unknown-voice")
        assert size == 65 * 1024 * 1024

    def test_format_size_returns_human_readable(self, manager: PiperModelManager):
        result = manager.format_size_for_display("ru_RU-dmitri-medium")
        assert "65 MB" in result
        assert "~" in result
