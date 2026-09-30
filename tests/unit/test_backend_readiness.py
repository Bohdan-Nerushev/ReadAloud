"""
Regression tests for M6 / H1: local backends are verified fail-fast before a task starts,
real failure reasons reach the user, and a dead Piper server does not cause retry storms.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.application.services.piper_setup_service import PiperPrerequisiteResult, PiperSetupService
from src.application.services.xtts_setup_service import XttsSetupService
from src.domain.exceptions import (
    BackendNotReadyException,
    PiperNotAvailableException,
    XttsReferenceAudioMissingException,
    is_fatal_setup_error,
)
from src.domain.models import TaskStatus, TtsBackend
from src.infrastructure.docker_manager import DockerManager
from src.infrastructure.piper_model_manager import PiperModelManager
from src.infrastructure.xtts_model_manager import XttsDiagnosticsResult, XttsModelManager
from tests.unit._lifecycle_helpers import make_config, make_controller, run_inline


# ---------------------------------------------------------------------------
# Piper setup service
# ---------------------------------------------------------------------------

def _piper(docker_ok=True, image_ok=True, port_open=False, conflict=False,
           model_present=True, language_supported=True):
    docker = MagicMock(spec=DockerManager)
    docker.is_docker_available.return_value = docker_ok
    docker.is_piper_image_available.return_value = image_ok
    docker.is_piper_port_open.side_effect = lambda *a, **k: port_open
    docker.is_port_occupied_by_other.return_value = conflict
    models = MagicMock(spec=PiperModelManager)
    models.is_language_supported.return_value = language_supported
    models.is_model_present.return_value = model_present
    models.get_voice_for_language.return_value = "en_US-voice"
    models.get_missing_model_files.return_value = []
    models.models_dir = Path("/fake/models")
    service = PiperSetupService(docker, models)
    return service, docker, models


class TestPiperEnsureReady:
    def test_running_container_returns_without_starting(self):
        service, docker, _ = _piper(port_open=True)
        service.ensure_ready("en", "male")
        docker.start_piper_compose.assert_not_called()

    def test_docker_missing_raises_with_reason(self):
        service, _, _ = _piper(docker_ok=False)
        with pytest.raises(BackendNotReadyException, match="Docker"):
            service.ensure_ready("en", "male")

    def test_unsupported_language_raises(self):
        service, _, _ = _piper(language_supported=False)
        with pytest.raises(BackendNotReadyException, match="not supported"):
            service.ensure_ready("zz", "male")

    def test_port_conflict_raises(self):
        service, _, _ = _piper(conflict=True)
        with pytest.raises(BackendNotReadyException):
            service.ensure_ready("en", "male")

    def test_start_failure_is_raised_not_swallowed(self):
        service, docker, _ = _piper()
        docker.start_piper_compose.side_effect = PiperNotAvailableException("compose exploded")
        errors = []
        service.setupError.connect(errors.append)
        with pytest.raises(BackendNotReadyException, match="compose exploded"):
            service.ensure_ready("en", "male")

    def test_port_never_opens_raises_timeout_as_not_ready(self):
        service, _, _ = _piper()
        service._wait_for_port_open = MagicMock(side_effect=TimeoutError("nope"))
        with pytest.raises(BackendNotReadyException, match="did not become available"):
            service.ensure_ready("en", "male")

    def test_image_pull_failure_is_raised(self):
        service, docker, _ = _piper(image_ok=False)
        docker.pull_piper_image.side_effect = PiperNotAvailableException("pull denied")
        with pytest.raises(BackendNotReadyException, match="pull denied"):
            service.ensure_ready("en", "male")
        docker.start_piper_compose.assert_not_called()

    def test_model_download_failure_is_raised(self):
        service, docker, models = _piper(model_present=False)
        models.download_model.side_effect = OSError("disk full")
        with pytest.raises(BackendNotReadyException, match="disk full"):
            service.ensure_ready("en", "male")
        docker.start_piper_compose.assert_not_called()

    def test_successful_start_waits_for_port(self):
        service, docker, _ = _piper()
        service._wait_for_port_open = MagicMock()
        service.ensure_ready("en", "male")
        docker.start_piper_compose.assert_called_once()
        service._wait_for_port_open.assert_called_once()


class TestPiperPublicMethodsStillReportToUi:
    """The UI workers rely on setupError signals; those must keep working."""

    def test_start_container_emits_setup_error_and_does_not_raise(self):
        service, docker, _ = _piper()
        docker.start_piper_compose.side_effect = PiperNotAvailableException("boom")
        errors, started = [], []
        service.setupError.connect(errors.append)
        service.containerStarted.connect(lambda: started.append(1))
        service.start_container("en", "male")
        assert errors and "boom" in errors[0]
        assert not started

    def test_pull_image_emits_setup_error(self):
        service, docker, _ = _piper()
        docker.pull_piper_image.side_effect = PiperNotAvailableException("nope")
        errors = []
        service.setupError.connect(errors.append)
        service.pull_image()
        assert errors and "nope" in errors[0]

    def test_download_model_emits_setup_error(self):
        service, _, models = _piper()
        models.download_model.side_effect = OSError("net down")
        errors = []
        service.setupError.connect(errors.append)
        service.download_model("en", "male")
        assert errors and "net down" in errors[0]


# ---------------------------------------------------------------------------
# XTTS setup service
# ---------------------------------------------------------------------------

def _diag(**over):
    base = dict(tts_package_installed=True, cuda_available=True, vram_free_gb=8.0,
                vram_total_gb=12.0, vram_sufficient=True, model_cached=True)
    base.update(over)
    return XttsDiagnosticsResult(**base)


def _xtts(diag, loaded=False, ref_error=None):
    manager = MagicMock(spec=XttsModelManager)
    manager.run_diagnostics.return_value = diag
    if ref_error:
        manager.get_reference_audio_path.side_effect = ref_error
    service = XttsSetupService(manager)
    service.is_model_loaded = MagicMock(return_value=loaded)
    return service, manager


class TestXttsEnsureReady:
    def test_ok(self):
        service, manager = _xtts(_diag())
        service.ensure_ready("en", "male")
        manager.get_reference_audio_path.assert_called_once_with("en", "male")

    def test_unsupported_language(self):
        service, _ = _xtts(_diag())
        with pytest.raises(BackendNotReadyException, match="not supported"):
            service.ensure_ready("fr", "male")

    def test_package_missing(self):
        service, _ = _xtts(_diag(tts_package_installed=False, error_message="TTS package missing"))
        with pytest.raises(BackendNotReadyException, match="TTS package missing"):
            service.ensure_ready("en", "male")

    def test_cuda_missing(self):
        service, _ = _xtts(_diag(cuda_available=False, error_message="CUDA not available"))
        with pytest.raises(BackendNotReadyException, match="CUDA"):
            service.ensure_ready("en", "male")

    def test_model_not_cached(self):
        service, _ = _xtts(_diag(model_cached=False))
        with pytest.raises(BackendNotReadyException, match="not downloaded"):
            service.ensure_ready("en", "male")

    def test_insufficient_vram_blocks_fresh_load(self):
        service, _ = _xtts(_diag(vram_sufficient=False, vram_free_gb=1.0, error_message="Low VRAM"), loaded=False)
        with pytest.raises(BackendNotReadyException, match="Low VRAM"):
            service.ensure_ready("en", "male")

    def test_low_free_vram_is_fine_when_model_already_loaded(self):
        """A loaded model itself occupies the VRAM; that must not block the next task."""
        service, _ = _xtts(_diag(vram_sufficient=False, vram_free_gb=1.0), loaded=True)
        service.ensure_ready("en", "male")

    def test_missing_speaker_wav_reports_path(self):
        err = XttsReferenceAudioMissingException("Speaker reference audio not found: /x/en/male.wav")
        service, _ = _xtts(_diag(), ref_error=err)
        with pytest.raises(BackendNotReadyException, match="en/male.wav"):
            service.ensure_ready("en", "male")


# ---------------------------------------------------------------------------
# Exception classification
# ---------------------------------------------------------------------------

class TestFatalClassification:
    @pytest.mark.parametrize("exc", [
        BackendNotReadyException("x"), PiperNotAvailableException("x"),
        XttsReferenceAudioMissingException("x"), ValueError("lang"), FileNotFoundError("f"),
    ])
    def test_fatal(self, exc):
        assert is_fatal_setup_error(exc)

    @pytest.mark.parametrize("exc", [RuntimeError("x"), TimeoutError(), ConnectionError()])
    def test_not_fatal(self, exc):
        assert not is_fatal_setup_error(exc)


# ---------------------------------------------------------------------------
# Controller: fail-fast, real reason, no generation on failure
# ---------------------------------------------------------------------------

def _controller_with_backend(tmp_path, backend, ensure_ready):
    controller, queue, _, generation, _ = make_controller(tmp_path / "state.json")
    piper, xtts = MagicMock(), MagicMock()
    target = piper if backend == TtsBackend.PIPER else xtts
    target.ensure_ready.side_effect = ensure_ready
    controller._piper_setup_service = piper
    controller._xtts_setup_service = xtts
    queue.add_task(make_config(tmp_path, "B", backend))
    task = queue.get_next_task()
    controller._is_stopped = False
    controller._chunks = []
    controller._audio_dir = str(tmp_path)
    return controller, queue, generation, task, target


class TestControllerFailFast:
    @pytest.mark.parametrize("backend", [TtsBackend.PIPER, TtsBackend.XTTS])
    def test_not_ready_fails_task_with_real_reason_and_never_generates(self, tmp_path, backend):
        controller, queue, generation, task, _ = _controller_with_backend(
            tmp_path, backend, BackendNotReadyException("Docker daemon is not running"))
        errors = []
        controller.errorOccurred.connect(errors.append)

        controller._start_generation_process()

        generation.start_generation.assert_not_called()
        assert task.status == TaskStatus.FAILED
        assert "Docker daemon is not running" in task.message
        assert any("Docker daemon is not running" in e for e in errors)

    def test_ready_backend_starts_generation(self, tmp_path):
        controller, _, generation, task, target = _controller_with_backend(
            tmp_path, TtsBackend.PIPER, None)
        controller._start_generation_process()
        target.ensure_ready.assert_called_once_with("en", "male")
        generation.start_generation.assert_called_once()

    def test_edge_backend_needs_no_preparation(self, tmp_path):
        controller, queue, _, generation, _ = make_controller(tmp_path / "state.json")
        queue.add_task(make_config(tmp_path, "E", TtsBackend.EDGE_TTS))
        queue.get_next_task()
        controller._is_stopped = False
        controller._chunks = []
        controller._audio_dir = str(tmp_path)
        controller._backend_runner = MagicMock()
        controller._start_generation_process()
        controller._backend_runner.assert_not_called()
        generation.start_generation.assert_called_once()

    def test_unexpected_exception_type_still_fails_task(self, tmp_path):
        controller, _, generation, task, _ = _controller_with_backend(
            tmp_path, TtsBackend.XTTS, RuntimeError("weird"))
        controller._start_generation_process()
        generation.start_generation.assert_not_called()
        assert task.status == TaskStatus.FAILED
        assert "weird" in task.message

    def test_status_message_shows_preparation(self, tmp_path):
        controller, queue, generation, task, _ = _controller_with_backend(tmp_path, TtsBackend.PIPER, None)
        seen = []
        controller._backend_runner = lambda fn, ok, err: seen.append(task.message)
        controller._start_generation_process()
        assert "Preparing" in seen[0]

    def test_cancelled_preparation_result_is_ignored(self, tmp_path):
        controller, _, generation, task, _ = _controller_with_backend(tmp_path, TtsBackend.PIPER, None)
        pending = []
        controller._backend_runner = lambda fn, ok, err: pending.append((fn, ok, err))
        controller._start_generation_process()
        controller._invalidate_backend_prep()          # user cancelled meanwhile
        fn, ok, err = pending[0]
        ok(fn())
        generation.start_generation.assert_not_called()

    def test_preparation_result_for_other_task_is_ignored(self, tmp_path):
        controller, queue, generation, task, _ = _controller_with_backend(tmp_path, TtsBackend.PIPER, None)
        pending = []
        controller._backend_runner = lambda fn, ok, err: pending.append((fn, ok, err))
        controller._start_generation_process()
        queue.discard_current_task()
        fn, ok, err = pending[0]
        err(BackendNotReadyException("late failure"))
        assert task.status != TaskStatus.FAILED
        generation.start_generation.assert_not_called()

    def test_gui_thread_is_not_used_for_preparation(self, tmp_path):
        """Default runner executes the readiness routine off the calling (GUI) thread."""
        import threading
        import time
        controller, _, generation, task, target = _controller_with_backend(tmp_path, TtsBackend.PIPER, None)
        threads = []
        target.ensure_ready.side_effect = lambda *a: threads.append(threading.current_thread())
        from src.application.app_controller import ApplicationController
        controller._backend_runner = controller._run_backend_in_thread
        controller._bridge.finished.disconnect()
        controller._bridge.finished.connect(lambda cb, arg: None)
        controller._start_generation_process()
        deadline = time.time() + 3
        while not threads and time.time() < deadline:
            time.sleep(0.01)
        assert threads and threads[0] is not threading.main_thread()


# ---------------------------------------------------------------------------
# Batch-level errors: real reason, no retry sweeps
# ---------------------------------------------------------------------------

class TestGenerationAborted:
    def _service(self, generator):
        from src.application.services.generation_service import GenerationService
        return GenerationService(generator)

    @pytest.mark.parametrize("exc", [
        XttsReferenceAudioMissingException("no speaker wav"),
        ValueError("Language 'zz' is not supported"),
        PiperNotAvailableException("server down"),
    ])
    def test_fatal_batch_error_emits_aborted_not_batch_failed(self, exc):
        generator = MagicMock()
        generator.generate_audio_batch.side_effect = exc
        service = self._service(generator)
        aborted, failed = [], []
        service.generationAborted.connect(aborted.append)
        service.batchFailed.connect(lambda *a: failed.append(a))
        service._generate_batch_safe([MagicMock(chunk_number=1)], "en", "male", "cid", 1)
        assert aborted and (getattr(exc, "message", None) or str(exc)) in aborted[0]
        assert not failed

    def test_generic_batch_error_remains_a_batch_failure(self):
        generator = MagicMock()
        generator.generate_audio_batch.side_effect = RuntimeError("transient glitch")
        service = self._service(generator)
        aborted, failed = [], []
        service.generationAborted.connect(aborted.append)
        service.batchFailed.connect(lambda *a: failed.append(a))
        service._generate_batch_safe([MagicMock(chunk_number=1)], "en", "male", "cid", 1)
        assert failed and not aborted

    def test_chunk_failure_reports_generator_last_error(self):
        generator = MagicMock()
        generator.last_error = "chunk 1: CUDA out of memory"
        generator.generate_audio_batch.return_value = [None]
        service = self._service(generator)
        reasons = []
        service.chunkFailed.connect(lambda n, r: reasons.append(r))
        service._generate_batch_safe([MagicMock(chunk_number=1)], "en", "male", "cid", 1)
        assert reasons == ["chunk 1: CUDA out of memory"]

    def test_controller_fails_task_immediately_on_abort(self, tmp_path):
        controller, queue, _, generation, _ = make_controller(tmp_path / "state.json")
        queue.add_task(make_config(tmp_path))
        task = queue.get_next_task()
        controller._is_stopped = False
        controller._on_generation_aborted("Speaker reference audio not found: /x.wav")
        assert task.status == TaskStatus.FAILED
        assert "Speaker reference audio not found" in task.message
        assert "could not be synthesized" not in task.message

    def test_controller_ignores_abort_when_stopped(self, tmp_path):
        controller, queue, _, generation, _ = make_controller(tmp_path / "state.json")
        queue.add_task(make_config(tmp_path))
        task = queue.get_next_task()
        controller._is_stopped = True
        controller._on_generation_aborted("late")
        assert task.status != TaskStatus.FAILED

    def test_aborted_signal_is_wired_to_controller(self):
        from src.application.services.generation_service import GenerationService
        assert hasattr(GenerationService, "generationAborted")
