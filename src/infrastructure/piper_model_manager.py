"""
Piper Model Manager.

Manages voice model files for the wyoming-piper TTS backend.
Models are stored locally as pairs of files:
  - <voice_name>.onnx     (neural network weights)
  - <voice_name>.onnx.json (synthesis configuration)

This manager only checks for model presence and reports missing files.
It does NOT download models automatically — that decision belongs to the user.

Design decisions:
  - Models directory is created automatically if absent (empty dir is fine).
  - A 0-byte file is treated as absent (incomplete download).
  - The manager is stateless — all state lives in the filesystem.
"""

import logging
from pathlib import Path
from typing import Callable, List, Optional

from src.domain.exceptions import PiperModelMissingException
from src.domain.piper_audio_generator import PIPER_VOICE_MAPPING, PIPER_SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

# Default local directory for Piper model files.
# Follows XDG base directory convention for Linux.
DEFAULT_MODELS_DIR = Path.home() / ".local" / "share" / "readaloud" / "piper_models"

# Required file extensions for each voice model
_MODEL_EXTENSIONS = (".onnx", ".onnx.json")

# Approximate model sizes in bytes for user guidance (rough estimates)
# Actual size varies per voice; these are used for UI display only.
_APPROXIMATE_MODEL_SIZES: dict[str, int] = {
    "x_low":  25 * 1024 * 1024,   # ~25 MB
    "low":    40 * 1024 * 1024,   # ~40 MB
    "medium": 65 * 1024 * 1024,   # ~65 MB
    "high":  100 * 1024 * 1024,   # ~100 MB
}


class PiperModelManager:
    """
    Checks the presence and integrity of locally downloaded Piper voice models.

    Responsibilities:
      - Verify that both model files (.onnx and .onnx.json) are present and non-empty.
      - Report which files are missing for user-facing error messages.
      - Ensure the models directory exists (created on first access).
      - Provide approximate model size for download confirmation dialogs.

    NOT responsible for:
      - Downloading models (delegated to user-triggered workflow).
      - Validating model content / ONNX schema.
    """

    def __init__(self, models_dir: Path = DEFAULT_MODELS_DIR) -> None:
        """
        Args:
            models_dir: Directory where voice model files are stored.
                        Created automatically if it does not exist.
        """
        self._models_dir = models_dir
        self._ensure_models_dir()

    @property
    def models_dir(self) -> Path:
        """The configured models directory path."""
        return self._models_dir

    # ------------------------------------------------------------------
    # Presence checks
    # ------------------------------------------------------------------

    def is_model_present(self, voice_name: str) -> bool:
        """
        Returns True if all required files for ``voice_name`` exist and are non-empty.

        Args:
            voice_name: Piper voice identifier, e.g. "ru_RU-dmitri-medium".
        """
        missing = self.get_missing_model_files(voice_name)
        return len(missing) == 0

    def get_missing_model_files(self, voice_name: str) -> List[str]:
        """
        Returns a list of missing or empty model file paths for ``voice_name``.

        An empty list means the model is fully present.

        Args:
            voice_name: Piper voice identifier.

        Returns:
            List of absolute file path strings that are missing or zero-length.
        """
        missing: List[str] = []
        for ext in _MODEL_EXTENSIONS:
            file_path = self._models_dir / f"{voice_name}{ext}"
            if not file_path.exists() or file_path.stat().st_size == 0:
                missing.append(str(file_path))
        return missing

    def validate_model_or_raise(self, voice_name: str) -> None:
        """
        Raises PiperModelMissingException if the model is not fully present.

        Args:
            voice_name: Piper voice identifier.

        Raises:
            PiperModelMissingException: Lists all missing files in the message.
        """
        missing = self.get_missing_model_files(voice_name)
        if missing:
            raise PiperModelMissingException(
                f"Voice model '{voice_name}' is not fully downloaded. "
                f"Missing files:\n" + "\n".join(f"  - {f}" for f in missing)
            )

    # ------------------------------------------------------------------
    # Voice resolution helpers
    # ------------------------------------------------------------------

    def get_voice_for_language(self, language: str, gender: str) -> str:
        """
        Returns the Piper voice name for the given language and gender.

        Args:
            language: ISO language code (e.g. "ru").
            gender:   "male" or "female".

        Raises:
            ValueError: If language is not supported by Piper.
        """
        if language not in PIPER_SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Language '{language}' is not supported by Piper. "
                f"Supported: {sorted(PIPER_SUPPORTED_LANGUAGES)}"
            )
        return PIPER_VOICE_MAPPING[language][gender]

    def is_language_supported(self, language: str) -> bool:
        """Returns True if Piper supports the given language."""
        return language in PIPER_SUPPORTED_LANGUAGES

    # ------------------------------------------------------------------
    # Size estimation (for download confirmation UI)
    # ------------------------------------------------------------------

    def estimate_model_size_bytes(self, voice_name: str) -> int:
        """
        Returns a rough estimate of the model download size in bytes.

        Uses the quality tier suffix (x_low / low / medium / high) from
        the voice name to pick a preset estimate.

        Args:
            voice_name: Piper voice identifier (e.g. "ru_RU-dmitri-medium").

        Returns:
            Estimated size in bytes. Returns the "medium" estimate as a fallback.
        """
        for quality, size in _APPROXIMATE_MODEL_SIZES.items():
            if voice_name.endswith(f"-{quality}"):
                return size
        return _APPROXIMATE_MODEL_SIZES["medium"]

    def format_size_for_display(self, voice_name: str) -> str:
        """Returns a human-readable size string, e.g. '65 MB'."""
        size_bytes = self.estimate_model_size_bytes(voice_name)
        size_mb = size_bytes // (1024 * 1024)
        return f"~{size_mb} MB"

    def download_model(
            self,
            voice_name: str,
            progress_callback: Optional[Callable[[str], None]] = None
    ) -> None:
        """
        Downloads .onnx and .onnx.json files for voice_name from Hugging Face.

        Args:
            voice_name: Piper voice identifier (e.g. "ru_RU-dmitri-medium").
            progress_callback: Optional callback receiving progress text messages.
        """
        parts = voice_name.split("-")
        if len(parts) < 3:
            raise ValueError(f"Invalid voice name format: {voice_name}")
        locale = parts[0]
        lang = locale.split("_")[0]
        name = parts[1]
        quality = parts[2]

        base_url = (
            f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            f"{lang}/{locale}/{name}/{quality}/{voice_name}"
        )

        import urllib.request
        for ext in _MODEL_EXTENSIONS:
            file_url = f"{base_url}{ext}"
            dest_path = self._models_dir / f"{voice_name}{ext}"
            temp_path = self._models_dir / f"{voice_name}{ext}.tmp"

            if progress_callback:
                progress_callback(f"Downloading {voice_name}{ext}...")

            logger.info("Downloading %s to %s", file_url, dest_path)
            urllib.request.urlretrieve(file_url, temp_path)
            temp_path.replace(dest_path)

        logger.info("Voice model '%s' downloaded successfully to %s", voice_name, self._models_dir)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_models_dir(self) -> None:
        """Creates the models directory if it does not exist."""
        try:
            self._models_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("Piper models directory ensured: %s", self._models_dir)
        except PermissionError as exc:
            logger.error(
                "Cannot create Piper models directory '%s': %s",
                self._models_dir, exc,
            )
            raise
