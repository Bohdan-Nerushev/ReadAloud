"""
Unit tests for PiperSetupService.

All Docker and filesystem operations are mocked.
Qt signals are tested via a simple signal spy helper.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from src.application.services.piper_setup_service import PiperSetupService, PiperPrerequisiteResult
from src.domain.exceptions import PiperNotAvailableException
from src.infrastructure.docker_manager import DockerManager
from src.infrastructure.piper_model_manager import PiperModelManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class SignalSpy:
    """Captures emissions from a Qt pyqtSignal for assertion."""
    def __init__(self):
        self.calls: List = []

    def slot(self, *args):
        self.calls.append(args)

    @property
    def count(self) -> int:
        return len(self.calls)

    def last_arg(self):
        return self.calls[-1][0] if self.calls else None


def make_docker_manager(
    docker_ok: bool = True,
    image_ok: bool = True,
    port_open: bool = False,
    port_conflict: bool = False,
) -> MagicMock:
    m = MagicMock(spec=DockerManager)
    m.is_docker_available.return_value = docker_ok
    m.is_piper_image_available.return_value = image_ok
    m.is_piper_port_open.return_value = port_open
    m.is_port_occupied_by_other.return_value = port_conflict
    return m


def make_model_manager(
    language_supported: bool = True,
    model_present: bool = True,
    voice: str = "ru_RU-dmitri-medium",
    missing_files: Optional[List[str]] = None,
) -> MagicMock:
    m = MagicMock(spec=PiperModelManager)
    m.is_language_supported.return_value = language_supported
    m.is_model_present.return_value = model_present
    m.get_voice_for_language.return_value = voice
    m.get_missing_model_files.return_value = missing_files or []
    m.format_size_for_display.return_value = "~65 MB"
    m.models_dir = Path("/fake/models")
    return m


@pytest.fixture
def service() -> PiperSetupService:
    docker = make_docker_manager()
    model = make_model_manager()
    return PiperSetupService(docker_manager=docker, model_manager=model)


# ---------------------------------------------------------------------------
# PiperPrerequisiteResult
# ---------------------------------------------------------------------------

class TestPiperPrerequisiteResult:
    def test_is_ready_all_true(self):
        result = PiperPrerequisiteResult(
            docker_available=True, image_available=True,
            model_present=True, port_conflict=False,
        )
        assert result.is_ready is True

    def test_is_ready_false_when_docker_missing(self):
        result = PiperPrerequisiteResult(docker_available=False, image_available=True, model_present=True)
        assert result.is_ready is False

    def test_is_ready_false_when_model_missing(self):
        result = PiperPrerequisiteResult(docker_available=True, image_available=True, model_present=False)
        assert result.is_ready is False

    def test_is_ready_false_when_port_conflict(self):
        result = PiperPrerequisiteResult(
            docker_available=True, image_available=True,
            model_present=True, port_conflict=True,
        )
        assert result.is_ready is False

    def test_can_start_container_requires_all_prerequisites(self):
        result = PiperPrerequisiteResult(
            docker_available=True, image_available=True,
            model_present=True, port_conflict=False,
        )
        assert result.can_start_container is True


# ---------------------------------------------------------------------------
# check_prerequisites
# ---------------------------------------------------------------------------

class TestCheckPrerequisites:
    def test_all_prerequisites_ok(self, service: PiperSetupService):
        spy = SignalSpy()
        service.prerequisiteCheckCompleted.connect(spy.slot)

        result = service.check_prerequisites("ru", "male")

        assert result.docker_available is True
        assert result.image_available is True
        assert result.model_present is True
        assert result.port_conflict is False
        assert result.is_ready is True
        assert spy.count == 1

    def test_docker_not_available(self):
        service = PiperSetupService(
            docker_manager=make_docker_manager(docker_ok=False),
            model_manager=make_model_manager(),
        )
        spy = SignalSpy()
        service.prerequisiteCheckCompleted.connect(spy.slot)

        result = service.check_prerequisites("ru", "male")

        assert result.docker_available is False
        assert result.is_ready is False
        assert "Docker" in result.error_message
        assert spy.count == 1

    def test_image_not_available(self):
        service = PiperSetupService(
            docker_manager=make_docker_manager(image_ok=False),
            model_manager=make_model_manager(),
        )
        result = service.check_prerequisites("ru", "male")

        assert result.image_available is False
        assert result.is_ready is False
        assert "rhasspy/wyoming-piper" in result.error_message

    def test_model_missing(self):
        service = PiperSetupService(
            docker_manager=make_docker_manager(),
            model_manager=make_model_manager(
                model_present=False,
                missing_files=["/fake/models/ru_RU-dmitri-medium.onnx"],
            ),
        )
        result = service.check_prerequisites("ru", "male")

        assert result.model_present is False
        assert len(result.missing_model_files) == 1
        assert result.is_ready is False

    def test_port_conflict_detected(self):
        service = PiperSetupService(
            docker_manager=make_docker_manager(port_conflict=True),
            model_manager=make_model_manager(),
        )
        result = service.check_prerequisites("ru", "male")

        assert result.port_conflict is True
        assert result.is_ready is False
        assert "10200" in result.error_message

    def test_container_running_detected(self):
        service = PiperSetupService(
            docker_manager=make_docker_manager(port_open=True),
            model_manager=make_model_manager(),
        )
        result = service.check_prerequisites("ru", "male")

        assert result.container_running is True

    def test_unsupported_language(self):
        service = PiperSetupService(
            docker_manager=make_docker_manager(),
            model_manager=make_model_manager(language_supported=False),
        )
        result = service.check_prerequisites("zz", "male")

        assert result.model_present is False


# ---------------------------------------------------------------------------
# start_container
# ---------------------------------------------------------------------------

class TestStartContainer:
    def test_start_calls_docker_manager(self):
        docker = make_docker_manager()
        model = make_model_manager()
        service = PiperSetupService(docker_manager=docker, model_manager=model)

        started_spy = SignalSpy()
        service.containerStarted.connect(started_spy.slot)

        with patch.object(service, "_wait_for_port_open"):
            service.start_container("ru", "male")

        docker.start_piper_compose.assert_called_once()
        # Extract env from the call — it is always passed as a keyword argument
        call_kwargs = docker.start_piper_compose.call_args.kwargs
        env = call_kwargs.get("env", {})
        assert "PIPER_VOICE" in env
        assert env["PIPER_VOICE"] == "ru_RU-dmitri-medium"

    def test_start_emits_container_started_on_success(self):
        docker = make_docker_manager()
        model = make_model_manager()
        service = PiperSetupService(docker_manager=docker, model_manager=model)

        started_spy = SignalSpy()
        service.containerStarted.connect(started_spy.slot)

        with patch.object(service, "_wait_for_port_open"):
            service.start_container("ru", "male")

        assert started_spy.count == 1

    def test_start_emits_setup_error_on_docker_failure(self):
        docker = make_docker_manager()
        docker.start_piper_compose.side_effect = PiperNotAvailableException("compose failed")
        model = make_model_manager()
        service = PiperSetupService(docker_manager=docker, model_manager=model)

        error_spy = SignalSpy()
        service.setupError.connect(error_spy.slot)

        service.start_container("ru", "male")

        assert error_spy.count == 1
        assert "compose failed" in error_spy.last_arg()

    def test_start_emits_error_on_port_timeout(self):
        docker = make_docker_manager()
        model = make_model_manager()
        service = PiperSetupService(docker_manager=docker, model_manager=model)

        error_spy = SignalSpy()
        service.setupError.connect(error_spy.slot)

        with patch.object(service, "_wait_for_port_open", side_effect=TimeoutError("port timeout")):
            service.start_container("ru", "male")

        assert error_spy.count == 1


# ---------------------------------------------------------------------------
# stop_container
# ---------------------------------------------------------------------------

class TestStopContainer:
    def test_stop_calls_docker_manager(self, service: PiperSetupService):
        stopped_spy = SignalSpy()
        service.containerStopped.connect(stopped_spy.slot)

        service.stop_container()

        service._docker_manager.stop_piper_compose.assert_called_once()
        assert stopped_spy.count == 1

    def test_stop_emits_error_on_exception(self, service: PiperSetupService):
        service._docker_manager.stop_piper_compose.side_effect = Exception("crash")
        error_spy = SignalSpy()
        service.setupError.connect(error_spy.slot)

        service.stop_container()

        assert error_spy.count == 1


# ---------------------------------------------------------------------------
# pull_image
# ---------------------------------------------------------------------------

class TestPullImage:
    def test_pull_calls_docker_manager(self, service: PiperSetupService):
        status_spy = SignalSpy()
        service.statusMessage.connect(status_spy.slot)

        service.pull_image()

        service._docker_manager.pull_piper_image.assert_called_once()

    def test_pull_emits_error_on_failure(self, service: PiperSetupService):
        service._docker_manager.pull_piper_image.side_effect = PiperNotAvailableException("no space")
        error_spy = SignalSpy()
        service.setupError.connect(error_spy.slot)

        service.pull_image()

        assert error_spy.count == 1
        assert "no space" in error_spy.last_arg()
