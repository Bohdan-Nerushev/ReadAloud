"""
Unit tests for DockerManager.

All Docker CLI calls are mocked via subprocess.run — no Docker installation required.
"""

import socket
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from src.domain.exceptions import PiperNotAvailableException
from src.infrastructure.docker_manager import DockerManager, PIPER_IMAGE, PIPER_DEFAULT_PORT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def compose_file(tmp_path: Path) -> Path:
    """Creates a dummy compose file so DockerManager doesn't raise on init."""
    f = tmp_path / "docker-compose.yml"
    f.write_text("services:\n  piper:\n    image: rhasspy/wyoming-piper\n")
    return f


@pytest.fixture
def manager(compose_file: Path) -> DockerManager:
    return DockerManager(compose_file=compose_file)


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> Mock:
    proc = Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# is_docker_available
# ---------------------------------------------------------------------------

class TestIsDockerAvailable:
    def test_returns_true_when_docker_responds(self, manager: DockerManager):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", return_value=_make_proc(0, "24.0.5")),
        ):
            assert manager.is_docker_available() is True

    def test_returns_false_when_not_in_path(self, manager: DockerManager):
        with patch("shutil.which", return_value=None):
            assert manager.is_docker_available() is False

    def test_returns_false_when_daemon_not_running(self, manager: DockerManager):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", return_value=_make_proc(1, "", "Cannot connect to daemon")),
        ):
            assert manager.is_docker_available() is False

    def test_returns_false_on_timeout(self, manager: DockerManager):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=10)),
        ):
            assert manager.is_docker_available() is False


# ---------------------------------------------------------------------------
# is_piper_image_available
# ---------------------------------------------------------------------------

class TestIsPiperImageAvailable:
    def test_returns_true_when_image_present(self, manager: DockerManager):
        with patch("subprocess.run", return_value=_make_proc(0, "sha256:abc123")):
            assert manager.is_piper_image_available() is True

    def test_returns_false_when_image_absent(self, manager: DockerManager):
        with patch("subprocess.run", return_value=_make_proc(1, "", "No such image")):
            assert manager.is_piper_image_available() is False

    def test_returns_false_on_os_error(self, manager: DockerManager):
        with patch("subprocess.run", side_effect=OSError("file not found")):
            assert manager.is_piper_image_available() is False


# ---------------------------------------------------------------------------
# is_piper_port_open
# ---------------------------------------------------------------------------

class TestIsPiperPortOpen:
    def test_returns_true_when_port_open(self, manager: DockerManager):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock()
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            assert manager.is_piper_port_open(10200) is True

    def test_returns_false_when_port_closed(self, manager: DockerManager):
        with patch("socket.create_connection", side_effect=ConnectionRefusedError()):
            assert manager.is_piper_port_open(10200) is False

    def test_returns_false_on_timeout(self, manager: DockerManager):
        with patch("socket.create_connection", side_effect=socket.timeout()):
            assert manager.is_piper_port_open(10200) is False


# ---------------------------------------------------------------------------
# pull_piper_image
# ---------------------------------------------------------------------------

class TestPullPiperImage:
    def test_pull_succeeds(self, manager: DockerManager):
        with (
            patch.object(manager, "is_docker_available", return_value=True),
            patch("subprocess.run", return_value=_make_proc(0)) as mock_run,
        ):
            manager.pull_piper_image()
            args = mock_run.call_args[0][0]
            assert "pull" in args
            assert PIPER_IMAGE in args

    def test_raises_when_docker_unavailable(self, manager: DockerManager):
        with patch.object(manager, "is_docker_available", return_value=False):
            with pytest.raises(PiperNotAvailableException, match="Docker is not available"):
                manager.pull_piper_image()

    def test_raises_when_pull_fails(self, manager: DockerManager):
        with (
            patch.object(manager, "is_docker_available", return_value=True),
            patch("subprocess.run", return_value=_make_proc(1, "", "pull access denied")),
        ):
            with pytest.raises(PiperNotAvailableException, match="Failed to pull"):
                manager.pull_piper_image()

    def test_raises_on_pull_timeout(self, manager: DockerManager):
        with (
            patch.object(manager, "is_docker_available", return_value=True),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=600)),
        ):
            with pytest.raises(PiperNotAvailableException, match="Timeout"):
                manager.pull_piper_image()


# ---------------------------------------------------------------------------
# start_piper_compose
# ---------------------------------------------------------------------------

class TestStartPiperCompose:
    def test_start_calls_compose_up(self, manager: DockerManager, compose_file: Path):
        with (
            patch.object(manager, "_compose_cmd", return_value=["docker", "compose", "-f", str(compose_file)]),
            patch.object(manager, "_run_compose", return_value=_make_proc(0)) as mock_run,
        ):
            manager.start_piper_compose(env={"PIPER_VOICE": "ru_RU-dmitri-medium"})
            called_cmd = mock_run.call_args[0][0]
            assert "up" in called_cmd

    def test_raises_when_compose_file_missing(self, manager: DockerManager):
        manager._compose_file = Path("/nonexistent/docker-compose.yml")
        with pytest.raises(PiperNotAvailableException, match="not found"):
            manager.start_piper_compose()

    def test_raises_with_logs_on_compose_failure(self, manager: DockerManager):
        with (
            patch.object(manager, "_compose_cmd", return_value=["docker", "compose", "-f", "f"]),
            patch.object(manager, "_run_compose", return_value=_make_proc(1, "", "OOM killer")),
            patch.object(manager, "get_container_logs", return_value="OOM: out of memory"),
        ):
            with pytest.raises(PiperNotAvailableException):
                manager.start_piper_compose()


# ---------------------------------------------------------------------------
# Compose command version detection
# ---------------------------------------------------------------------------

class TestComposeCommand:
    def test_uses_docker_compose_v2(self, manager: DockerManager):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch.object(manager, "_supports_compose_v2", return_value=True),
        ):
            cmd = manager._compose_cmd()
            assert cmd[:2] == ["docker", "compose"]

    def test_falls_back_to_v1(self, manager: DockerManager):
        with (
            patch("shutil.which", side_effect=lambda x: "/usr/bin/docker-compose" if x == "docker-compose" else None),
            patch.object(manager, "_supports_compose_v2", return_value=False),
        ):
            cmd = manager._compose_cmd()
            assert cmd[0] == "docker-compose"

    def test_raises_when_neither_available(self, manager: DockerManager):
        with (
            patch("shutil.which", return_value=None),
            patch.object(manager, "_supports_compose_v2", return_value=False),
        ):
            with pytest.raises(PiperNotAvailableException):
                manager._compose_cmd()


# ---------------------------------------------------------------------------
# check_prerequisites convenience method
# ---------------------------------------------------------------------------

class TestCheckPrerequisites:
    def test_returns_true_when_all_ok(self, manager: DockerManager):
        with (
            patch.object(manager, "is_docker_available", return_value=True),
            patch.object(manager, "is_piper_image_available", return_value=True),
        ):
            ok, msg = manager.check_prerequisites()
            assert ok is True
            assert msg == ""

    def test_returns_false_no_docker(self, manager: DockerManager):
        with patch.object(manager, "is_docker_available", return_value=False):
            ok, msg = manager.check_prerequisites()
            assert ok is False
            assert "Docker" in msg

    def test_returns_false_no_image(self, manager: DockerManager):
        with (
            patch.object(manager, "is_docker_available", return_value=True),
            patch.object(manager, "is_piper_image_available", return_value=False),
        ):
            ok, msg = manager.check_prerequisites()
            assert ok is False
            assert PIPER_IMAGE in msg
