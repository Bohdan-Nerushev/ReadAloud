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
# Compose project / container owned by ReadAloud; nothing else is ever stopped.
PIPER_COMPOSE_PROJECT = "readaloud"
PIPER_CONTAINER_NAME = "readaloud-piper"
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

    def is_piper_container_running(self) -> bool:
        """
        Returns True if the 'readaloud-piper' container exists and is running.
        """
        try:
            result = subprocess.run(
                ["docker", "inspect", PIPER_CONTAINER_NAME, "--format", "{{.State.Running}}"],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
            )
            return result.returncode == 0 and result.stdout.strip().lower() == "true"
        except Exception as exc:
            logger.debug("Piper container running check failed: %s", exc)
            return False

    def get_piper_container_port(self) -> Optional[int]:
        """
        If the 'readaloud-piper' container is running, returns its host port mapped to 10200/tcp.
        Returns None if container is not running or port mapping cannot be found.
        """
        try:
            result = subprocess.run(
                ["docker", "port", PIPER_CONTAINER_NAME, "10200/tcp"],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    parts = line.strip().rsplit(":", 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        return int(parts[1])
        except Exception as exc:
            logger.debug("Failed to retrieve Piper container port: %s", exc)
        return None

    def find_available_port(self, start_port: int = PIPER_DEFAULT_PORT, max_port: int = 10300) -> Optional[int]:
        """
        Finds the first available TCP port on localhost starting from start_port up to max_port.
        If all ports in range are taken, attempts to bind port 0 to get an OS-assigned free port.
        Returns None if no port could be bound.
        """
        for port in range(start_port, max_port):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    continue
            except (ConnectionRefusedError, socket.timeout, OSError):
                pass

            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", port))
                    return port
            except OSError:
                continue

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", 0))
                return s.getsockname()[1]
        except OSError:
            return None

    def is_port_occupied_by_other(self, port: int = PIPER_DEFAULT_PORT) -> bool:
        """
        Returns True if the port is open but NOT by our Piper container or compose stack.
        """
        if not self.is_piper_port_open(port):
            return False

        # If readaloud-piper container is running, check if it owns this port
        if self.is_piper_container_running():
            container_port = self.get_piper_container_port()
            if container_port == port:
                return False

        # Check docker compose services running under our compose project
        try:
            result = subprocess.run(
                self._compose_cmd() + ["ps", "--services", "--filter", "status=running"],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
                cwd=str(self._compose_file.parent),
            )
            if bool(result.stdout.strip()):
                return False

            result_plain = subprocess.run(
                ["docker", "compose", "-f", str(self._compose_file), "ps", "--services", "--filter", "status=running"],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
                cwd=str(self._compose_file.parent),
            )
            if bool(result_plain.stdout.strip()):
                return False
        except Exception:
            pass

        return True

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
        self._cleanup_stale_container()
        cmd = self._compose_cmd() + ["up", "-d", "--remove-orphans"]
        result = self._run_compose(cmd, extra_env=env)
        if result.returncode != 0:
            if "Conflict. The container name" in result.stderr and PIPER_CONTAINER_NAME in result.stderr:
                logger.warning(
                    "Container name collision detected for '%s'. Force-removing stale container and retrying compose up...",
                    PIPER_CONTAINER_NAME,
                )
                self._remove_stale_container(PIPER_CONTAINER_NAME)
                result = self._run_compose(cmd, extra_env=env)

            if result.returncode != 0:
                logs = self.get_container_logs(lines=30)
                raise PiperNotAvailableException(
                    f"docker compose up failed (exit {result.returncode}): "
                    f"{result.stderr.strip()}\nLogs:\n{logs}"
                )
        logger.info("Piper container started.")

    def _cleanup_stale_container(self) -> None:
        """
        Removes any existing container named PIPER_CONTAINER_NAME if it is not
        currently running (e.g. exited, stopped, or created by a different compose project).
        This prevents Docker daemon name conflict errors during compose up.
        """
        try:
            inspect = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}}", PIPER_CONTAINER_NAME],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
            )
            if inspect.returncode == 0:
                status = inspect.stdout.strip().lower()
                if status != "running":
                    logger.info(
                        "Found non-running container '%s' (status: %s). Removing it before compose up...",
                        PIPER_CONTAINER_NAME, status,
                    )
                    self._remove_stale_container(PIPER_CONTAINER_NAME)
        except Exception as exc:
            logger.debug("Failed to inspect/cleanup stale container '%s': %s", PIPER_CONTAINER_NAME, exc)

    def _remove_stale_container(self, name: str) -> None:
        """Removes a container by name via docker rm -f."""
        try:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                text=True,
                timeout=DOCKER_COMMAND_TIMEOUT,
            )
        except Exception as exc:
            logger.debug("Failed to remove container '%s': %s", name, exc)

    def stop_piper_compose(self) -> None:
        """
        Stops the Piper service via docker compose.

        Only the compose project owned by ReadAloud (PIPER_COMPOSE_PROJECT) is
        touched; containers started by the user from the same image are left alone.

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
            return ["docker", "compose", "-p", PIPER_COMPOSE_PROJECT, "-f", str(self._compose_file)]
        if shutil.which("docker-compose"):
            return ["docker-compose", "-p", PIPER_COMPOSE_PROJECT, "-f", str(self._compose_file)]
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
