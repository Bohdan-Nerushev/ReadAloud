"""
Piper Setup Service.

Orchestrates the prerequisite checks and container lifecycle for the Piper TTS backend.
This service is the single entry point for the UI to interact with Piper setup,
keeping Docker and model management details out of the GUI layer.

Responsibilities:
  - Check all prerequisites (Docker, image, model files) in one call.
  - Start/stop the wyoming-piper container via DockerManager.
  - Provide structured results that the UI can render directly.
  - Emit Qt signals so the UI does not need to poll.

NOT responsible for:
  - Downloading model files (user-triggered, not automated).
  - Generating audio (that is PiperAudioGenerator's job).
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from src.domain.exceptions import PiperNotAvailableException, PiperModelMissingException
from src.infrastructure.docker_manager import DockerManager, PIPER_DEFAULT_PORT
from src.infrastructure.piper_model_manager import PiperModelManager

logger = logging.getLogger(__name__)


@dataclass
class PiperPrerequisiteResult:
    """
    Structured result of a Piper prerequisite check.

    All boolean fields are False by default; they are set to True only if
    the corresponding check passed. This prevents accidentally using an
    uninitialised result as "everything is fine".
    """
    docker_available: bool = False
    image_available: bool = False
    container_running: bool = False
    model_present: bool = False
    missing_model_files: List[str] = field(default_factory=list)
    port_conflict: bool = False
    error_message: Optional[str] = None

    @property
    def is_ready(self) -> bool:
        """
        Returns True only if ALL prerequisites are satisfied and no port conflict exists.

        A ready state means the Piper backend can be started and used immediately.
        """
        return (
            self.docker_available
            and self.image_available
            and self.model_present
            and not self.port_conflict
        )

    @property
    def can_start_container(self) -> bool:
        """True if Docker is available and no port conflict on 10200."""
        return self.docker_available and not self.port_conflict


class PiperSetupService(QObject):
    # Emitted after check_prerequisites() completes
    prerequisiteCheckCompleted = pyqtSignal(object)  # PiperPrerequisiteResult
    # Emitted when container start/stop succeeds
    containerStarted = pyqtSignal()
    containerStopped = pyqtSignal()
    setupError = pyqtSignal(str)
    # Emitted during long operations (e.g. image pull) with progress text
    statusMessage = pyqtSignal(str)

    def __init__(
            self,
            docker_manager: DockerManager,
            model_manager: PiperModelManager,
            piper_port: int = PIPER_DEFAULT_PORT,
    ) -> None:
        """
        Args:
            docker_manager: Manages Docker CLI interactions.
            model_manager:  Checks local model file presence.
            piper_port:     Port where the wyoming-piper container listens.
        """
        super().__init__()
        self._docker_manager = docker_manager
        self._model_manager = model_manager
        self._piper_port = piper_port

    # ------------------------------------------------------------------
    # Prerequisite checks
    # ------------------------------------------------------------------

    def check_prerequisites(self, language: str, gender: str) -> PiperPrerequisiteResult:
        """
        Runs all prerequisite checks for the Piper backend.

        Checks:
          1. Docker binary is in PATH and daemon responds.
          2. rhasspy/wyoming-piper image is present locally.
          3. Voice model files for (language, gender) are present and non-empty.
          4. No port conflict on 10200 from a non-Piper process.
          5. Whether the Piper container is already running.

        This method is safe to call from a background thread.
        Emits ``prerequisiteCheckCompleted`` when done.

        Args:
            language: ISO language code to check model for (e.g. "ru").
            gender:   "male" or "female".

        Returns:
            PiperPrerequisiteResult with all fields populated.
        """
        result = PiperPrerequisiteResult()

        # 1. Docker availability
        result.docker_available = self._docker_manager.is_docker_available()
        if not result.docker_available:
            result.error_message = (
                "Docker is not available. Ensure Docker Engine is installed and daemon is running."
            )
            self.prerequisiteCheckCompleted.emit(result)
            return result

        # 2. Image availability
        result.image_available = self._docker_manager.is_piper_image_available()

        # 3. Container running check
        result.container_running = self._docker_manager.is_piper_port_open(self._piper_port)

        if result.container_running:
            # If the Piper container is open and responding, it is actively serving synthesis.
            result.port_conflict = False
            result.model_present = True
            result.error_message = ""
        else:
            if not result.image_available:
                result.error_message = (
                    "The 'rhasspy/wyoming-piper' Docker image is not present locally "
                    "(will be pulled automatically)."
                )

            # Check port conflict only if port is open but NOT by running Piper container
            result.port_conflict = self._docker_manager.is_port_occupied_by_other(self._piper_port)
            if result.port_conflict:
                result.error_message = f"Port {self._piper_port} is occupied by another process."

            # Model presence check
            if not self._model_manager.is_language_supported(language):
                result.model_present = False
                result.error_message = (
                    f"Language '{language}' is not supported by Piper. "
                    f"Switch to Edge TTS or choose a supported language."
                )
            else:
                voice = self._model_manager.get_voice_for_language(language, gender)
                result.model_present = self._model_manager.is_model_present(voice)
                if not result.model_present:
                    result.missing_model_files = self._model_manager.get_missing_model_files(voice)

        logger.info(
            "Piper prerequisite check: docker=%s image=%s model=%s conflict=%s running=%s",
            result.docker_available, result.image_available,
            result.model_present, result.port_conflict, result.container_running,
        )
        self.prerequisiteCheckCompleted.emit(result)
        return result

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    def start_container(self, language: str, gender: str) -> None:
        """
        Starts the wyoming-piper Docker container for the given voice.
        Automatically pulls Docker image or downloads voice model if missing.

        Must be called from a background thread (blocks until container is up).
        Emits ``containerStarted`` on success or ``setupError`` on failure.

        Args:
            language: ISO language code.
            gender:   "male" or "female".
        """
        try:
            # 1. Auto-pull Docker image if missing
            if not self._docker_manager.is_piper_image_available():
                self.pull_image()

            # 2. Auto-download voice model files if missing
            voice = self._model_manager.get_voice_for_language(language, gender)
            if not self._model_manager.is_model_present(voice):
                self.download_model(language, gender)

            models_dir = str(self._model_manager.models_dir)

            self.statusMessage.emit(f"Starting Piper container with voice '{voice}' ...")
            logger.info("Starting Piper container: voice=%s models_dir=%s", voice, models_dir)

            env = {
                "PIPER_VOICE": voice,
                "PIPER_MODELS_DIR": models_dir,
                "PIPER_PORT": str(self._piper_port),
            }
            self._docker_manager.start_piper_compose(env=env)

            # Poll until the port is open (up to 30 seconds)
            self._wait_for_port_open(timeout=30.0, check_interval=1.0)

            logger.info("Piper container is ready on port %d.", self._piper_port)
            self.containerStarted.emit()

        except PiperNotAvailableException as exc:
            logger.error("Failed to start Piper container: %s", exc)
            self.setupError.emit(str(exc))
        except TimeoutError:
            msg = (
                f"Piper container did not become available on port {self._piper_port} "
                "within 30 seconds. Check container logs for details."
            )
            logger.error(msg)
            self.setupError.emit(msg)
        except Exception as exc:
            logger.error("Unexpected error starting Piper container: %s", exc, exc_info=True)
            self.setupError.emit(f"Unexpected error: {exc}")

    def stop_container(self) -> None:
        """
        Stops the wyoming-piper Docker container.

        Must be called from a background thread. Emits ``containerStopped`` on
        success or ``setupError`` on failure (but stop failure is non-critical).
        """
        try:
            self.statusMessage.emit("Stopping Piper container ...")
            self._docker_manager.stop_piper_compose()
            logger.info("Piper container stopped.")
            self.containerStopped.emit()
        except Exception as exc:
            logger.warning("Failed to stop Piper container: %s", exc)
            self.setupError.emit(f"Could not stop Piper container: {exc}")

    def pull_image(self) -> None:
        """
        Pulls the rhasspy/wyoming-piper Docker image.

        Blocks for several minutes on a slow connection.
        Must be called from a background thread.
        Emits ``statusMessage`` during progress and ``setupError`` on failure.
        """
        try:
            self.statusMessage.emit(
                "Downloading rhasspy/wyoming-piper image (~1 GB) ... this may take several minutes."
            )
            self._docker_manager.pull_piper_image()
            self.statusMessage.emit("Image downloaded successfully.")
        except PiperNotAvailableException as exc:
            logger.error("Image pull failed: %s", exc)
            self.setupError.emit(str(exc))

    def download_model(self, language: str, gender: str) -> None:
        """
        Downloads voice model files for (language, gender) from Hugging Face.

        Must be called from a background thread.
        Emits ``statusMessage`` during progress and ``setupError`` on failure.
        """
        try:
            voice = self._model_manager.get_voice_for_language(language, gender)
            self.statusMessage.emit(f"Downloading voice model '{voice}' from Hugging Face...")
            self._model_manager.download_model(
                voice,
                progress_callback=lambda msg: self.statusMessage.emit(msg)
            )
            self.statusMessage.emit(f"Voice model '{voice}' downloaded successfully.")
        except Exception as exc:
            logger.error("Failed to download voice model: %s", exc, exc_info=True)
            self.setupError.emit(f"Could not download voice model: {exc}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wait_for_port_open(self, timeout: float, check_interval: float) -> None:
        """
        Polls until the Piper port is open or timeout expires.

        Raises:
            TimeoutError: If the port does not open within ``timeout`` seconds.
        """
        import time
        elapsed = 0.0
        while elapsed < timeout:
            if self._docker_manager.is_piper_port_open(self._piper_port):
                return
            time.sleep(check_interval)
            elapsed += check_interval
        raise TimeoutError(
            f"Port {self._piper_port} did not open within {timeout:.0f} seconds."
        )
