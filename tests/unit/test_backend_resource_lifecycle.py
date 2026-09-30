"""
Regression tests for H8 / H9: backend resource ownership and lifecycle.

  * Piper is stopped only if a Piper task ran, and only ReadAloud's own compose
    project/container is ever touched.
  * XTTS model is unloaded automatically when no XTTS task remains.
  * shutdown() is idempotent.
  * The "Unload from VRAM" button follows is_model_loaded and is blocked during synthesis.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.unit._lifecycle_helpers import make_config, make_controller

from src.domain.models import TaskStatus, TtsBackend
from src.infrastructure import docker_manager as dm
from src.infrastructure.docker_manager import DockerManager

REPO = Path(__file__).resolve().parents[2]


class _ControllerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.controller, self.queue, _p, self.gen, _a) = make_controller(self.tmp / "state.json")
        self.piper = MagicMock()
        self.xtts = MagicMock()
        self.xtts.is_model_loaded.return_value = True
        self.controller._piper_setup_service = self.piper
        self.controller._xtts_setup_service = self.xtts

    def _add(self, backend, name="T"):
        return self.queue.add_task(make_config(self.tmp, name, backend))


class TestReleaseIdleBackends(_ControllerBase):

    def test_docker_untouched_when_no_piper_task_ever_ran(self):
        self._add(TtsBackend.EDGE_TTS)
        self.queue.get_next_task()
        self.queue.finalize_current_task()
        self.controller._release_idle_backends()
        self.piper.stop_container.assert_not_called()

    def test_piper_stopped_once_after_last_piper_task(self):
        self.controller._piper_used = True
        self.controller._release_idle_backends()
        self.piper.stop_container.assert_called_once()
        self.controller._release_idle_backends()
        self.piper.stop_container.assert_called_once()

    def test_piper_kept_while_another_piper_task_pending(self):
        self.controller._piper_used = True
        self._add(TtsBackend.PIPER)
        self.controller._release_idle_backends()
        self.piper.stop_container.assert_not_called()

    def test_xtts_unloaded_when_no_xtts_task_remains(self):
        self.controller._release_idle_backends()
        self.xtts.unload_model.assert_called_once()

    def test_xtts_kept_while_xtts_task_pending(self):
        self._add(TtsBackend.XTTS)
        self.controller._release_idle_backends()
        self.xtts.unload_model.assert_not_called()

    def test_xtts_unload_skipped_when_not_loaded(self):
        self.xtts.is_model_loaded.return_value = False
        self.controller._release_idle_backends()
        self.xtts.unload_model.assert_not_called()

    def test_failing_unload_does_not_raise(self):
        self.xtts.unload_model.side_effect = RuntimeError("cuda")
        self.controller._release_idle_backends()

    def test_finalize_task_releases_backends(self):
        self.controller._piper_used = True
        self.controller._finalize_task()
        self.piper.stop_container.assert_called_once()
        self.xtts.unload_model.assert_called_once()


class TestShutdownIdempotent(_ControllerBase):

    def test_second_shutdown_is_noop(self):
        self.controller.shutdown()
        self.controller.shutdown()
        self.controller.shutdown()
        self.assertEqual(self.piper.stop_container.call_count, 1)
        self.assertEqual(self.xtts.unload_model.call_count, 1)

    def test_shutdown_skips_unload_when_model_not_loaded(self):
        self.xtts.is_model_loaded.return_value = False
        self.controller.shutdown()
        self.xtts.unload_model.assert_not_called()


class TestDockerOwnership(unittest.TestCase):

    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        f = tmp / "docker-compose.yml"
        f.write_text("services:\n  piper:\n    image: rhasspy/wyoming-piper\n")
        self.manager = DockerManager(compose_file=f)

    def _proc(self, rc=0):
        p = MagicMock()
        p.returncode, p.stdout, p.stderr = rc, "", ""
        return p

    def test_compose_commands_use_dedicated_project_name(self):
        with patch("shutil.which", return_value="/usr/bin/docker"), \
                patch.object(DockerManager, "_supports_compose_v2", return_value=True):
            cmd = self.manager._compose_cmd()
        self.assertIn("-p", cmd)
        self.assertEqual(cmd[cmd.index("-p") + 1], dm.PIPER_COMPOSE_PROJECT)

    def test_stop_never_touches_foreign_containers(self):
        with patch.object(self.manager, "_compose_cmd", return_value=["docker", "compose"]), \
                patch.object(self.manager, "_run_compose", return_value=self._proc()) as run, \
                patch("subprocess.run") as sp:
            self.manager.stop_piper_compose()
        run.assert_called_once()
        sp.assert_not_called()

    def test_stop_failure_does_not_raise(self):
        with patch.object(self.manager, "_compose_cmd", return_value=["docker", "compose"]), \
                patch.object(self.manager, "_run_compose", return_value=self._proc(1)):
            self.manager.stop_piper_compose()

    def test_compose_file_names_container_and_disables_autorestart(self):
        text = (REPO / "docker" / "piper" / "docker-compose.yml").read_text()
        self.assertIn(f"container_name: {dm.PIPER_CONTAINER_NAME}", text)
        self.assertIn('restart: "no"', text)
        self.assertNotIn("unless-stopped", text)

    def test_source_no_longer_filters_containers_by_image(self):
        src = (REPO / "src" / "infrastructure" / "docker_manager.py").read_text()
        self.assertNotIn("ancestor=", src)


class TestXttsSetupServiceLoadedState(unittest.TestCase):

    def test_reflects_generator_state(self):
        from src.application.services.xtts_setup_service import XttsSetupService
        svc = XttsSetupService(model_manager=MagicMock())
        self.assertFalse(svc.is_model_loaded())
        gen = MagicMock()
        gen.is_model_loaded = True
        svc.set_generator(gen)
        self.assertTrue(svc.is_model_loaded())
        gen.is_model_loaded = False
        self.assertFalse(svc.is_model_loaded())


class TestUnloadButtonRule(unittest.TestCase):

    def setUp(self):
        from src.gui.widgets.tts_backend_selector import TtsBackendSelector
        self.rule = TtsBackendSelector.xtts_unload_enabled

    def test_enabled_only_when_loaded_idle_and_not_busy(self):
        self.assertTrue(self.rule(True, False, False))
        self.assertFalse(self.rule(False, False, False))
        self.assertFalse(self.rule(True, True, False))
        self.assertFalse(self.rule(True, False, True))


class TestSelectorUnloadButton(unittest.TestCase):

    def _selector(self, loaded):
        from src.gui.widgets.tts_backend_selector import TtsBackendSelector
        xtts = MagicMock()
        xtts.is_model_loaded.return_value = loaded
        with patch.object(TtsBackendSelector, "_run_piper_check"), \
                patch.object(TtsBackendSelector, "_run_xtts_check"):
            sel = TtsBackendSelector(MagicMock(), xtts)
        return sel, xtts

    def test_button_enabled_when_loaded_even_without_free_vram(self):
        from src.infrastructure.xtts_model_manager import XttsDiagnosticsResult
        sel, _ = self._selector(True)
        result = MagicMock(spec=XttsDiagnosticsResult)
        result.tts_package_installed, result.model_cached, result.is_ready = True, True, False
        sel._update_xtts_button_states(result)
        self.assertTrue(sel._btn_xtts_unload.isEnabled())
        self.assertIn("loaded", sel._xtts_status_loaded.text())

    def test_button_disabled_when_not_loaded(self):
        sel, _ = self._selector(False)
        sel.refresh_xtts_model_state()
        self.assertFalse(sel._btn_xtts_unload.isEnabled())
        self.assertIn("not loaded", sel._xtts_status_loaded.text())

    def test_button_blocked_during_generation_and_restored_after(self):
        sel, _ = self._selector(True)
        sel.set_generation_active(True)
        self.assertFalse(sel._btn_xtts_unload.isEnabled())
        sel.set_generation_active(False)
        self.assertTrue(sel._btn_xtts_unload.isEnabled())

    def test_busy_action_disables_then_reenables_by_state(self):
        sel, _ = self._selector(False)
        sel._set_xtts_buttons_busy(True)
        sel._set_xtts_buttons_busy(False)
        self.assertFalse(sel._btn_xtts_unload.isEnabled())


if __name__ == "__main__":
    unittest.main()
