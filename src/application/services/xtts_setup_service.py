"""
XTTS Setup Service.

Orchestrates prerequisite checks and model lifecycle for the Coqui XTTS-v2 backend.
This service is the single entry point for the UI to interact with XTTS setup,
keeping CUDA, model manager, and generator details out of the GUI layer.

Responsibilities:
  - Run all prerequisite diagnostics in one call (TTS package, CUDA, VRAM, model cache).
  - Trigger model weight download (blocking, intended for background threads).
  - Delegate model unload to XttsAudioGenerator.
  - Emit Qt signals so the GUI does not need to poll.

NOT responsible for:
  - Synthesising audio (XttsAudioGenerator).
  - Resolving speaker reference WAVs per-request (XttsModelManager).
"""

import logging
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from src.domain.exceptions import (
    BackendNotReadyException,
    XttsNotAvailableException,
    XttsReferenceAudioMissingException,
)
from src.infrastructure.xtts_model_manager import (
    XTTS_SUPPORTED_LANGUAGES,
    XttsDiagnosticsResult,
    XttsModelManager,
)

if TYPE_CHECKING:
    from src.domain.xtts_audio_generator import XttsAudioGenerator

logger = logging.getLogger(__name__)


class XttsSetupService(QObject):
    """
    Application-layer service for XTTS-v2 setup orchestration.

    All methods that perform I/O or GPU operations are safe to call from a
    background QThread; they emit signals to communicate results to the GUI.

    Signals:
        prerequisiteCheckCompleted(XttsDiagnosticsResult): Emitted after check_prerequisites().
        modelDownloadCompleted():                           Emitted when weights are downloaded.
        modelUnloaded():                                    Emitted after unload_model().
        setupError(str):                                    Emitted on any setup failure.
        statusMessage(str):                                 Emitted with progress text.
    """

    prerequisiteCheckCompleted = pyqtSignal(object)  # XttsDiagnosticsResult
    modelDownloadCompleted = pyqtSignal()
    modelUnloaded = pyqtSignal()
    setupError = pyqtSignal(str)
    statusMessage = pyqtSignal(str)

    def __init__(
            self,
            model_manager: XttsModelManager,
            generator: Optional["XttsAudioGenerator"] = None,
    ) -> None:
        """
        Args:
            model_manager: Provides diagnostics and speaker WAV resolution.
            generator:     The lazy-loaded generator instance. May be None if
                           the generator has not been created yet; in that case
                           unload_model() is a no-op.
        """
        super().__init__()
        self._model_manager = model_manager
        self._generator = generator

    def set_generator(self, generator: "XttsAudioGenerator") -> None:
        """
        Injects the generator reference after it has been created by the IoC container.

        This setter is needed because XttsAudioGenerator is created lazily, but
        XttsSetupService may be created first.
        """
        self._generator = generator

    # ------------------------------------------------------------------
    # Prerequisite checks
    # ------------------------------------------------------------------

    def check_prerequisites(self) -> XttsDiagnosticsResult:
        """
        Runs all XTTS prerequisite checks and returns a structured result.

        Checks:
          1. TTS Python package importable.
          2. CUDA available (torch.cuda.is_available()).
          3. Free VRAM >= 4 GB.
          4. XTTS-v2 model weights cached locally.
          5. Speaker reference WAV files present.

        Safe to call from a background thread.
        Emits ``prerequisiteCheckCompleted`` when done.

        Returns:
            XttsDiagnosticsResult with all fields populated.
        """
        result = self._model_manager.run_diagnostics()
        self.prerequisiteCheckCompleted.emit(result)
        return result

    # ------------------------------------------------------------------
    # Model download
    # ------------------------------------------------------------------

    def download_model(self) -> None:
        """
        Downloads XTTS-v2 model weights from the Coqui model hub.

        Blocks for several minutes on a slow connection (~1.8 GB download).
        Must be called from a background thread.

        Emits ``statusMessage`` during progress and ``setupError`` on failure.
        Emits ``modelDownloadCompleted`` on success.
        """
        try:
            self.statusMessage.emit(
                "Downloading XTTS-v2 model weights (~1.8 GB). This may take several minutes..."
            )
            logger.info("Starting XTTS-v2 model download.")
            self._trigger_model_download()
            logger.info("XTTS-v2 model downloaded successfully.")
            self.statusMessage.emit("XTTS-v2 model downloaded successfully.")
            self.modelDownloadCompleted.emit()
        except XttsNotAvailableException as exc:
            logger.error("XTTS model download failed: %s", exc)
            self.setupError.emit(str(exc))
        except Exception as exc:
            logger.error("Unexpected error during XTTS model download: %s", exc, exc_info=True)
            self.setupError.emit(f"Unexpected error during model download: {exc}")

    def _trigger_model_download(self) -> None:
        """
        Initiates model download by instantiating TTS with the model name.

        The Coqui TTS library automatically downloads weights on first instantiation
        if they are not cached. We create a temporary instance solely to trigger
        the download, then discard it. The actual loaded model is managed by
        XttsAudioGenerator._ensure_model_loaded().
        """
        try:
            import os
            os.environ["COQUI_TOS_AGREED"] = "1"
            from TTS.api import TTS  # noqa: PLC0415 — intentional lazy import
            logger.info("Triggering XTTS-v2 download via TTS library with COQUI_TOS_AGREED=1.")
            # gpu=False during download to avoid loading weights into VRAM.
            TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2",
                progress_bar=True, gpu=False)
        except ImportError as exc:
            raise XttsNotAvailableException(
                "The 'TTS' package is not installed. "
                "Run: pip install -r xtts_requirements.txt"
            ) from exc

    # ------------------------------------------------------------------
    # Model unload
    # ------------------------------------------------------------------

    def ensure_ready(self, language: str, gender: str) -> XttsDiagnosticsResult:
        """
        Fail-fast check run before an XTTS task starts.

        Raises:
            BackendNotReadyException: with the real reason when the task cannot work
                (unsupported language, missing TTS package / CUDA / model weights /
                speaker reference audio, or not enough free VRAM for a fresh load).
        """
        if language not in XTTS_SUPPORTED_LANGUAGES:
            raise BackendNotReadyException(
                f"Language '{language}' is not supported by XTTS-v2. "
                f"Supported: {', '.join(sorted(XTTS_SUPPORTED_LANGUAGES))}."
            )
        result = self._model_manager.run_diagnostics()
        loaded = self.is_model_loaded()

        if not result.tts_package_installed or not result.cuda_available:
            raise BackendNotReadyException(result.error_message or "XTTS-v2 prerequisites are not met.")
        if not result.model_cached:
            raise BackendNotReadyException(
                "XTTS-v2 model weights are not downloaded. "
                "Use 'Download Model' in the XTTS panel first."
            )
        # A loaded model itself occupies the VRAM, so low free VRAM is only a problem for a fresh load.
        if not loaded and not result.vram_sufficient:
            raise BackendNotReadyException(result.error_message or "Insufficient free VRAM for XTTS-v2.")
        try:
            self._model_manager.get_reference_audio_path(language, gender)
        except XttsReferenceAudioMissingException as exc:
            raise BackendNotReadyException(exc.message, cause=exc) from exc
        return result

    def is_model_loaded(self) -> bool:
        """True if the generator currently holds the XTTS model in memory."""
        return self._generator is not None and bool(self._generator.is_model_loaded)

    def unload_model(self) -> None:
        """
        Unloads the XTTS model from GPU memory and flushes the CUDA cache.

        Safe to call even if the model was never loaded.
        Emits ``modelUnloaded`` on success or ``setupError`` on failure.
        """
        try:
            if self._generator is not None:
                self._generator.unload_model()
            else:
                # Generator not yet created — just flush the cache defensively.
                self._model_manager.flush_gpu_cache()
            logger.info("XTTS model unloaded via setup service.")
            self.modelUnloaded.emit()
        except Exception as exc:
            logger.warning("Error unloading XTTS model: %s", exc)
            self.setupError.emit(f"Could not unload XTTS model: {exc}")
