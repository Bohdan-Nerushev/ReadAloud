"""
Docker Manager.

Provides a thin wrapper around Docker CLI commands needed to manage the
rhasspy/wyoming-piper container. All operations are blocking (subprocess-based)
and are intended to be called from background threads, not the UI thread.

Design decisions:
  - Uses subprocess rather than the docker-py SDK to avoid adding a heavy
    dependency. docker-py requires a running socket which complicates testing.
  - Supports both `docker compose` (v2, plugin) and `docker-compose` (v1, legacy).
  - Port availability is checked via a non-blocking TCP connect, not via
    `docker ps`, so it works even when the container is managed externally.
"""

import json
import logging
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from src.domain.exceptions import PiperNotAvailableException

logger = logging.getLogger(__name__)

PIPER_IMAGE = "rhasspy/wyoming-piper"
PIPER_DEFAULT_PORT = 10200
DOCKER_COMMAND_TIMEOUT = 30  # seconds for non-pull commands
DOCKER_PULL_TIMEOUT = 600    # seconds for image pull


class DockerManager:
    """
    Manages Docker operations required for the Piper TTS backend.

    All methods are safe to call from any thread. They perform no I/O on the
    Qt event loop. Errors are raised as PiperNotAvailableException so callers
    can surface them in the UI without catching subprocess exceptions.
    """

    def __init__(self, compose_file: Optional[Path] = None) -> None:
        """
        Args:
            compose_file: Path to the docker-compose.yml used for Piper.
                          Defaults to the bundled docker/piper/docker-compose.yml.
        """
        if compose_file is None:
            # Resolve relative to the project root (two levels up from src/infrastructure/)
            project_root = Path(__file__).parent.parent.parent
            compose_file = project_root / "docker" / "piper" / "docker-compose.yml"
        self._compose_file = compose_file

    # ------------------------------------------------------------------
    # Availability checks
    # ------------------------------------------------------------------

    def is_docker_available(self) -> bool:
        """
        Returns True if the docker binary is in PATH and the daemon responds.

        Does NOT raise — returns False on any failure so callers can
        branch without try/except.
        """
        if shutil.which("docker") is None:
            logger.debug("Docker binary not found in PATH.")
            return False

        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
            )
            available = result.returncode == 0
            if not available:
                logger.debug("docker info failed: %s", result.stderr.strip())
            return available
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.debug("Docker availability check failed: %s", exc)
            return False

    def is_piper_image_available(self) -> bool:
        """
        Returns True if the rhasspy/wyoming-piper image exists locally.

        Does NOT raise — returns False on any failure.
        """
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", PIPER_IMAGE, "--format", "{{.Id}}"],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
            )
            available = result.returncode == 0
            if not available:
                logger.debug("Piper image not found locally: %s", result.stderr.strip())
            return available
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.debug("Piper image check failed: %s", exc)
            return False

    def is_piper_port_open(self, port: int = PIPER_DEFAULT_PORT) -> bool:
        """
        Returns True if something is listening on 127.0.0.1:port.

        Uses a non-blocking TCP connect; does not require Docker CLI.
        """
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2.0):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False

    def is_port_occupied_by_other(self, port: int = PIPER_DEFAULT_PORT) -> bool:
        """
        Returns True if the port is open but NOT by our Piper compose stack.

        Used to detect port conflicts before starting the container.
        This check is a best-effort heuristic: it checks if the port is open
        but the compose project has no running containers.
        """
        if not self.is_piper_port_open(port):
            return False
        # Port is open — check if it's our container
        try:
            result = subprocess.run(
                self._compose_cmd() + ["ps", "--services", "--filter", "status=running"],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
                cwd=str(self._compose_file.parent),
            )
            our_services_running = bool(result.stdout.strip())
            return not our_services_running
        except Exception:
            # Cannot determine — assume it belongs to us
            return False

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    def pull_piper_image(self) -> None:
        """
        Pulls the rhasspy/wyoming-piper image from Docker Hub.

        This method blocks until the pull completes (may take several minutes
        on a slow connection). It must be called from a background thread.

        Raises:
            PiperNotAvailableException: If Docker is not available or pull fails.
        """
        if not self.is_docker_available():
            raise PiperNotAvailableException(
                "Docker is not available. Install Docker Engine and ensure the daemon is running."
            )

        logger.info("Pulling Docker image: %s ...", PIPER_IMAGE)
        try:
            result = subprocess.run(
                ["docker", "pull", PIPER_IMAGE],
                capture_output=True,
                text=True,
                timeout=DOCKER_PULL_TIMEOUT,
            )
            if result.returncode != 0:
                raise PiperNotAvailableException(
                    f"Failed to pull image '{PIPER_IMAGE}': {result.stderr.strip()}"
                )
            logger.info("Image '%s' pulled successfully.", PIPER_IMAGE)
        except subprocess.TimeoutExpired as e:
            raise PiperNotAvailableException(
                f"Timeout pulling image '{PIPER_IMAGE}' after {DOCKER_PULL_TIMEOUT}s."
            ) from e

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    def start_piper_compose(self, env: Optional[dict] = None) -> None:
        """
        Starts the Piper service via docker compose.

        Args:
            env: Optional dict of environment variables injected into the
                 compose process (e.g. PIPER_VOICE, PIPER_MODELS_DIR).

        Raises:
            PiperNotAvailableException: If the compose file is missing or
                                         docker compose returns a non-zero exit code.
        """
        if not self._compose_file.exists():
            raise PiperNotAvailableException(
                f"Docker Compose file not found: {self._compose_file}"
            )

        logger.info("Starting Piper container via docker compose ...")
        cmd = self._compose_cmd() + ["up", "-d", "--remove-orphans"]
        result = self._run_compose(cmd, extra_env=env)
        if result.returncode != 0:
            logs = self.get_container_logs(lines=30)
            raise PiperNotAvailableException(
                f"docker compose up failed (exit {result.returncode}): "
                f"{result.stderr.strip()}\nLogs:\n{logs}"
            )
        logger.info("Piper container started.")

    def stop_piper_compose(self) -> None:
        """
        Stops the Piper service via docker compose and terminates any running
        rhasspy/wyoming-piper containers (including standalone docker run instances).

        Does NOT raise on failure — logs a warning instead, so the app
        shutdown is not blocked by a stop failure.
        """
        if self._compose_file.exists():
            logger.info("Stopping Piper container via docker compose ...")
            cmd = self._compose_cmd() + ["down"]
            result = self._run_compose(cmd)
            if result.returncode != 0:
                logger.warning(
                    "docker compose down returned exit code %d: %s",
                    result.returncode, result.stderr.strip(),
                )

        if shutil.which("docker"):
            try:
                res = subprocess.run(
                    ["docker", "ps", "--filter", f"ancestor={PIPER_IMAGE}", "--format", "{{.ID}}"],
                    capture_output=True,
                    text=True,
                    timeout=DOCKER_COMMAND_TIMEOUT,
                )
                container_ids = [c.strip() for c in res.stdout.strip().splitlines() if c.strip()]
                for cid in container_ids:
                    logger.info("Stopping leftover Piper container: %s", cid)
                    subprocess.run(
                        ["docker", "stop", cid],
                        capture_output=True,
                        text=True,
                        timeout=DOCKER_COMMAND_TIMEOUT,
                    )
            except Exception as exc:
                logger.warning("Failed to stop leftover Piper containers: %s", exc)

    def get_container_logs(self, lines: int = 50) -> str:
        """
        Returns the last ``lines`` lines of the Piper container logs.

        Returns an empty string on any error (used for diagnostics only).
        """
        try:
            cmd = self._compose_cmd() + ["logs", "--tail", str(lines), "piper"]
            result = self._run_compose(cmd)
            return result.stdout.strip()
        except Exception as exc:
            logger.debug("Could not retrieve container logs: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def check_prerequisites(self) -> Tuple[bool, str]:
        """
        Convenience check: validates Docker + image availability.

        Returns:
            (ok: bool, error_message: str) — error_message is empty when ok=True.
        """
        if not self.is_docker_available():
            return False, (
                "Docker is not available. "
                "Install Docker Engine and ensure the daemon is running."
            )
        if not self.is_piper_image_available():
            return False, (
                f"Docker image '{PIPER_IMAGE}' is not present locally. "
                "Pull it via the 'Download' button or run: "
                f"docker pull {PIPER_IMAGE}"
            )
        return True, ""

    def _compose_cmd(self) -> list:
        """
        Returns the docker compose command prefix.

        Prefers `docker compose` (v2 plugin); falls back to `docker-compose` (v1 legacy).
        """
        if shutil.which("docker") and self._supports_compose_v2():
            return ["docker", "compose", "-f", str(self._compose_file)]
        if shutil.which("docker-compose"):
            return ["docker-compose", "-f", str(self._compose_file)]
        raise PiperNotAvailableException(
            "Neither 'docker compose' (v2) nor 'docker-compose' (v1) is available."
        )

    @staticmethod
    def _supports_compose_v2() -> bool:
        """Returns True if docker supports the 'compose' subcommand."""
        try:
            result = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_compose(
            self,
            cmd: list,
            extra_env: Optional[dict] = None,
    ) -> subprocess.CompletedProcess:
        """Runs a compose command with optional env overrides."""
        import os
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DOCKER_COMMAND_TIMEOUT,
            env=env,
            cwd=str(self._compose_file.parent),
        )
