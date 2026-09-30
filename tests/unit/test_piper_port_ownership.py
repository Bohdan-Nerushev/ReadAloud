"""An open Piper port only proves our container when no foreign process owns it."""

from unittest.mock import MagicMock

import pytest

from src.application.services.piper_setup_service import PiperSetupService
from src.domain.exceptions import BackendNotReadyException


def make_service(port_open: bool, foreign: bool):
    docker = MagicMock()
    docker.is_docker_available.return_value = True
    docker.is_piper_image_available.return_value = True
    docker.is_piper_port_open.return_value = port_open
    docker.is_port_occupied_by_other.return_value = foreign
    models = MagicMock()
    models.is_language_supported.return_value = True
    models.get_voice_for_language.return_value = "en_US-ryan-medium"
    models.is_model_present.return_value = True
    return PiperSetupService(docker_manager=docker, model_manager=models, piper_port=10200), docker


def test_open_port_owned_by_our_container_counts_as_running():
    service, _ = make_service(port_open=True, foreign=False)
    result = service.check_prerequisites("en", "male")
    assert result.container_running and not result.port_conflict


def test_open_port_owned_by_a_foreign_process_is_a_conflict_not_a_running_container():
    service, _ = make_service(port_open=True, foreign=True)
    result = service.check_prerequisites("en", "male")
    assert result.port_conflict and not result.container_running
    assert "10200" in result.error_message


def test_ensure_ready_refuses_to_use_a_foreign_listener():
    service, docker = make_service(port_open=True, foreign=True)
    with pytest.raises(BackendNotReadyException) as info:
        service.ensure_ready("en", "male")
    assert "occupied" in str(info.value)
    docker.start_piper_compose.assert_not_called()


def test_closed_port_is_neither_running_nor_a_conflict():
    service, docker = make_service(port_open=False, foreign=False)
    result = service.check_prerequisites("en", "male")
    assert not result.container_running and not result.port_conflict
