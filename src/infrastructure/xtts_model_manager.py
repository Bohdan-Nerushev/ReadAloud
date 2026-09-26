"""
XTTS Model Manager.

Infrastructure component responsible for:
  - Checking CUDA availability via PyTorch.
  - Verifying free VRAM meets the FP16 minimum threshold (default 4 GB).
  - Detecting whether the TTS Python package is installed.
  - Detecting whether the XTTS-v2 model weights are cached locally.
  - Resolving paths to speaker reference WAV files (for voice cloning).
  - Releasing GPU memory via torch.cuda.empty_cache().

This manager is stateless — all state lives in the filesystem or GPU runtime.
It does NOT load or unload the model itself; that is XttsAudioGenerator's job.

Design decisions:
  - All torch/TTS imports are deferred (inside methods) so that importing
    this module does not crash if neither package is installed.
  - The TTS package stores cached models under ~/.local/share/tts/ on Linux.
  - Speaker reference WAVs are resolved from a configurable base directory
    (default: <project_root>/data/xtts_speakers/).
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from src.domain.exceptions import XttsReferenceAudioMissingException

logger = logging.getLogger(__name__)

# Default VRAM minimum for XTTS-v2 in FP16 mode.
# The model itself uses ~3.2 GB; 4 GB leaves ~0.8 GB headroom for activations.
MIN_VRAM_GB: float = 4.0

# TTS cache directory name as used by the Coqui TTS library on Linux.
_TTS_CACHE_DIR = Path.home() / ".local" / "share" / "tts"

# Model-specific cache subdirectory name (dots replaced with dashes by TTS library).
_XTTS_MODEL_CACHE_SUBDIR = "tts_models--multilingual--multi-dataset--xtts_v2"

# Canonical XTTS-v2 model identifier used by the TTS API.
XTTS_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

# Languages supported by XTTS-v2 that ReadAloud exposes.
XTTS_SUPPORTED_LANGUAGES = frozenset({"uk", "en", "de", "ru"})

# Mapping from ISO language codes to XTTS API language identifiers.
XTTS_LANGUAGE_MAP: dict[str, str] = {
    "uk": "uk",
    "en": "en",
    "de": "de",
    "ru": "ru",
}


@dataclass
class XttsDiagnosticsResult:
    """
    Structured result of an XTTS prerequisite diagnostic run.

    All boolean fields default to False so that a partially-filled result
    is never accidentally treated as "everything is ready".
    """
    tts_package_installed: bool = False
    cuda_available: bool = False
    vram_free_gb: float = 0.0
    vram_total_gb: float = 0.0
    vram_sufficient: bool = False
    model_cached: bool = False
    error_message: Optional[str] = None
    missing_speaker_files: list = field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        """
        Returns True only when all hard prerequisites are satisfied.

        Does NOT check for speaker reference files — those are resolved
        per-synthesis-request, not at startup.
        """
        return (
            self.tts_package_installed
            and self.cuda_available
            and self.vram_sufficient
            and self.model_cached
        )


class XttsModelManager:
    """
    Checks runtime prerequisites and resolves resources for the XTTS-v2 backend.

    Responsibilities:
      - Verify TTS package is importable.
      - Check CUDA availability and free VRAM via PyTorch.
      - Confirm the XTTS-v2 model weights are cached locally.
      - Resolve and validate speaker reference WAV paths.
      - Expose a helper to flush GPU memory cache.

    NOT responsible for:
      - Loading/unloading the model (XttsAudioGenerator).
      - Downloading model weights (XttsSetupService).
      - Audio synthesis.
    """

    def __init__(
            self,
            speakers_dir: Optional[Path] = None,
            min_vram_gb: float = MIN_VRAM_GB,
    ) -> None:
        """
        Args:
            speakers_dir: Base directory for speaker reference WAV files.
                          Defaults to <project_root>/data/xtts_speakers/.
            min_vram_gb:  Minimum free VRAM in GB required for FP16 inference.
        """
        if speakers_dir is None:
            # Resolve relative to the project root (two levels up from src/infrastructure/).
            project_root = Path(__file__).parent.parent.parent
            speakers_dir = project_root / "src" / "resource" / "xtts_speakers"
        self._speakers_dir = speakers_dir
        self._min_vram_gb = min_vram_gb

    @property
    def speakers_dir(self) -> Path:
        """The configured speaker reference WAV directory."""
        return self._speakers_dir

    # ------------------------------------------------------------------
    # Full diagnostic run
    # ------------------------------------------------------------------

    def run_diagnostics(self) -> XttsDiagnosticsResult:
        """
        Runs all prerequisite checks and returns a structured result.

        Safe to call from a background thread.
        """
        result = XttsDiagnosticsResult()

        result.tts_package_installed = self.is_tts_package_installed()
        if not result.tts_package_installed:
            result.error_message = (
                "The 'TTS' Python package is not installed. "
                "Run: pip install TTS>=0.22.0"
            )
            return result

        result.cuda_available = self.check_cuda_available()
        if not result.cuda_available:
            result.error_message = (
                "CUDA is not available. Ensure an NVIDIA GPU is present, "
                "drivers are installed, and PyTorch was built with CUDA support."
            )
            return result

        vram_free, vram_total = self.get_vram_info()
        result.vram_free_gb = vram_free
        result.vram_total_gb = vram_total
        result.vram_sufficient = vram_free >= self._min_vram_gb
        if not result.vram_sufficient:
            result.error_message = (
                f"Insufficient free VRAM: {vram_free:.1f} GB free, "
                f"{self._min_vram_gb:.1f} GB required for FP16 inference. "
                f"Close other GPU-intensive applications and try again."
            )

        result.model_cached = self.is_model_cached()
        if not result.model_cached:
            msg = (
                "XTTS-v2 model weights are not cached locally. "
                "Click 'Download Model' to fetch them (~1.8 GB)."
            )
            result.error_message = result.error_message or msg

        result.missing_speaker_files = self._find_missing_speaker_files()

        logger.info(
            "XTTS diagnostics: tts=%s cuda=%s vram_free=%.1fGB vram_ok=%s model=%s missing_speakers=%s",
            result.tts_package_installed,
            result.cuda_available,
            result.vram_free_gb,
            result.vram_sufficient,
            result.model_cached,
            result.missing_speaker_files,
        )
        return result

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def is_tts_package_installed(self) -> bool:
        """Returns True if the Coqui TTS Python package can be imported."""
        try:
            import importlib.util
            return importlib.util.find_spec("TTS") is not None
        except Exception:
            return False

    def check_cuda_available(self) -> bool:
        """Returns True if PyTorch detects a usable CUDA device."""
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False
        except Exception as exc:
            logger.warning("Unexpected error checking CUDA availability: %s", exc)
            return False

    def get_vram_info(self) -> Tuple[float, float]:
        """
        Returns (free_gb, total_gb) for the default CUDA device.

        Returns (0.0, 0.0) if CUDA is not available or the query fails.
        """
        try:
            import torch
            if not torch.cuda.is_available():
                return 0.0, 0.0
            free_bytes, total_bytes = torch.cuda.mem_get_info(device=0)
            _gb = 1024 ** 3
            return free_bytes / _gb, total_bytes / _gb
        except ImportError:
            return 0.0, 0.0
        except Exception as exc:
            logger.warning("Failed to query VRAM info: %s", exc)
            return 0.0, 0.0

    def check_vram_sufficient(self) -> bool:
        """Returns True if free VRAM on device 0 meets the minimum threshold."""
        free_gb, _ = self.get_vram_info()
        return free_gb >= self._min_vram_gb

    def is_model_cached(self) -> bool:
        """
        Returns True if the XTTS-v2 model directory exists in the TTS cache.

        The TTS library stores downloaded models under ~/.local/share/tts/.
        An empty directory is treated as absent (incomplete download).
        """
        model_dir = _TTS_CACHE_DIR / _XTTS_MODEL_CACHE_SUBDIR
        if not model_dir.exists() or not model_dir.is_dir():
            return False
        # Require at least one non-empty file inside the cache directory.
        return any(
            f.is_file() and f.stat().st_size > 0
            for f in model_dir.rglob("*")
        )

    # ------------------------------------------------------------------
    # Speaker reference WAV resolution
    # ------------------------------------------------------------------

    def get_reference_audio_path(self, language: str, gender: str) -> Path:
        """
        Returns the absolute path to the speaker reference WAV file.

        Args:
            language: ISO language code (e.g. "uk", "en").
            gender:   "male" or "female".

        Returns:
            Path to the WAV file.

        Raises:
            XttsReferenceAudioMissingException: If the file is missing or empty.
        """
        wav_path = self._speakers_dir / language / f"{gender}.wav"
        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise XttsReferenceAudioMissingException(
                f"Speaker reference audio not found: {wav_path}. "
                f"Place a WAV file (5-30 s, clear speech) at this path to enable "
                f"voice cloning for language='{language}', gender='{gender}'."
            )
        return wav_path

    def _find_missing_speaker_files(self) -> list:
        """
        Returns a list of missing speaker WAV paths for all supported languages/genders.
        Used in diagnostics to warn the user proactively.
        """
        missing = []
        for lang in sorted(XTTS_SUPPORTED_LANGUAGES):
            for gender in ("male", "female"):
                wav_path = self._speakers_dir / lang / f"{gender}.wav"
                if not wav_path.exists() or wav_path.stat().st_size == 0:
                    missing.append(str(wav_path))
        return missing

    # ------------------------------------------------------------------
    # GPU memory management helpers
    # ------------------------------------------------------------------

    @staticmethod
    def flush_gpu_cache() -> None:
        """
        Calls torch.cuda.empty_cache() to release cached-but-unused GPU memory.

        Should be called after unloading the XTTS model to free VRAM for
        other processes or for a subsequent model reload.
        """
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.debug("CUDA memory cache flushed.")
        except ImportError:
            pass
        except Exception as exc:
            logger.warning("Failed to flush CUDA cache: %s", exc)

    def is_language_supported(self, language: str) -> bool:
        """Returns True if the language is supported by the XTTS-v2 backend."""
        return language in XTTS_SUPPORTED_LANGUAGES
