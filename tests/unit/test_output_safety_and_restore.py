"""
Regression tests for M1 (restore starts queue), M4 (atomic final MP3), M5 (temp dirs location)
and the UI part of M6 (error reason shown in backend status panels).
"""

import dataclasses
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.application.app_controller import ApplicationController, PreparationWorker
from src.application.services.persistence_service import PersistenceService
from src.application.services.queue_service import QueueService
from src.domain.audio_assembler import AudioAssembler
from src.domain.models import GenerationTask, TaskStatus
from src.domain.text_chunker import TextChunker
from src.domain.text_processor import TextProcessor
from src.infrastructure.file_manager import FileManager
from tests.unit._lifecycle_helpers import make_config, make_controller


# ---------------------------------------------------------------------------
# M1: restore_state
# ---------------------------------------------------------------------------

class TestRestoreStartsQueue:
    def _persist(self, tmp_path, statuses):
        state = tmp_path / "state.json"
        persistence = PersistenceService(str(state))
        tasks = []
        for i, status in enumerate(statuses):
            t = GenerationTask(config=make_config(tmp_path, f"T{i}"))
            t.status = status
            tasks.append(t)
        assert persistence.save_state(tasks)
        return state, tasks

    def test_pending_tasks_start_without_user_action(self, tmp_path):
        state, tasks = self._persist(tmp_path, [TaskStatus.PENDING, TaskStatus.PENDING])
        controller, queue, *_ = make_controller(state)
        controller._start_task = MagicMock()
        controller.restore_state()
        controller._start_task.assert_called_once()
        started = controller._start_task.call_args[0][0]
        assert str(started.id) == str(tasks[0].id)
        assert [str(t.id) for t in queue.get_all_tasks()][0] == str(tasks[0].id)

    def test_remaining_tasks_stay_queued_as_waiting(self, tmp_path):
        state, tasks = self._persist(tmp_path, [TaskStatus.PENDING, TaskStatus.PENDING])
        controller, queue, *_ = make_controller(state)
        controller._start_task = MagicMock()
        controller.restore_state()
        waiting = [t for t in queue.get_all_tasks() if t is not queue.get_current_task()]
        assert len(waiting) == 1 and waiting[0].status == TaskStatus.PENDING

    def test_interrupted_task_is_restored_paused_and_not_auto_started(self, tmp_path):
        state, tasks = self._persist(tmp_path, [TaskStatus.PROCESSING, TaskStatus.PENDING])
        controller, queue, *_ = make_controller(state)
        controller._start_task = MagicMock()
        controller.restore_state()
        current = queue.get_current_task()
        assert current.status == TaskStatus.PAUSED
        # a paused current task blocks the queue until the user resumes it
        controller._start_task.assert_not_called()

    def test_failed_tasks_go_to_history_and_are_not_started(self, tmp_path):
        state, tasks = self._persist(tmp_path, [TaskStatus.FAILED])
        controller, queue, *_ = make_controller(state)
        controller._start_task = MagicMock()
        controller.restore_state()
        controller._start_task.assert_not_called()
        assert queue.find_task(str(tasks[0].id)) is not None

    def test_empty_state_is_a_noop(self, tmp_path):
        controller, *_ = make_controller(tmp_path / "missing.json")
        controller._start_task = MagicMock()
        controller.restore_state()
        controller._start_task.assert_not_called()

    def test_controller_uses_public_queue_api_only(self):
        src = Path("src/application/app_controller.py").read_text(encoding="utf-8")
        assert "_queue_service._" not in src

    def test_queue_restore_methods(self):
        queue = QueueService()
        a = GenerationTask(config=MagicMock())
        b = GenerationTask(config=MagicMock())
        queue.restore_current_task(a)
        queue.restore_pending_task(b)
        assert queue.get_current_task() is a
        assert b in queue.get_all_tasks()


# ---------------------------------------------------------------------------
# M4: atomic final MP3
# ---------------------------------------------------------------------------

def _chunk_files(tmp_path, n=2):
    files = []
    for i in range(1, n + 1):
        p = tmp_path / f"{i}.mp3"
        p.write_bytes(b"ID3data")
        files.append(str(p))
    return files


def _fake_popen_writing(dest_holder, returncode=0, payload=b"MP3"):
    """Popen stand-in: 'ffmpeg' writes to the destination argument (last argv item)."""
    def factory(cmd, *a, **k):
        dest = Path(cmd[-1])
        dest_holder.append(dest)
        dest.write_bytes(payload)
        proc = MagicMock()
        proc.returncode = returncode
        proc.poll.return_value = returncode
        proc.stderr.readline.return_value = ""
        proc.stderr.read.return_value = "ffmpeg boom"
        return proc
    return factory


class TestAtomicFinalMp3:
    def test_ffmpeg_targets_temp_name_not_final(self, tmp_path):
        final = tmp_path / "final.mp3"
        dests = []
        with patch("subprocess.Popen", side_effect=_fake_popen_writing(dests)), \
                patch("subprocess.run", return_value=MagicMock(stdout="1.0")):
            AudioAssembler().assemble_audio(_chunk_files(tmp_path), str(final), durations=[1.0, 1.0])
        assert dests[0].name == "final.mp3.tmp"

    def test_success_publishes_final_and_removes_tmp(self, tmp_path):
        final = tmp_path / "final.mp3"
        with patch("subprocess.Popen", side_effect=_fake_popen_writing([])), \
                patch("subprocess.run", return_value=MagicMock(stdout="1.0")):
            AudioAssembler().assemble_audio(_chunk_files(tmp_path), str(final), durations=[1.0, 1.0])
        assert final.read_bytes() == b"MP3"
        assert not (tmp_path / "final.mp3.tmp").exists()

    def test_ffmpeg_failure_leaves_no_final_and_no_tmp(self, tmp_path):
        final = tmp_path / "final.mp3"
        with patch("subprocess.Popen", side_effect=_fake_popen_writing([], returncode=1, payload=b"PARTIAL")), \
                patch("subprocess.run", return_value=MagicMock(stdout="1.0")):
            with pytest.raises(Exception, match="ffmpeg"):
                AudioAssembler().assemble_audio(_chunk_files(tmp_path), str(final), durations=[1.0, 1.0])
        assert not final.exists()
        assert not (tmp_path / "final.mp3.tmp").exists()

    def test_cancellation_leaves_no_partial_file(self, tmp_path):
        final = tmp_path / "final.mp3"
        with patch("subprocess.Popen", side_effect=_fake_popen_writing([], returncode=-15, payload=b"PARTIAL")), \
                patch("subprocess.run", return_value=MagicMock(stdout="1.0")):
            with pytest.raises(Exception):
                AudioAssembler().assemble_audio(_chunk_files(tmp_path), str(final), durations=[1.0, 1.0])
        assert not final.exists() and not (tmp_path / "final.mp3.tmp").exists()

    def test_failed_run_does_not_destroy_previous_good_output(self, tmp_path):
        final = tmp_path / "final.mp3"
        final.write_bytes(b"OLD-GOOD")
        with patch("subprocess.Popen", side_effect=_fake_popen_writing([], returncode=1)), \
                patch("subprocess.run", return_value=MagicMock(stdout="1.0")):
            with pytest.raises(Exception):
                AudioAssembler().assemble_audio(_chunk_files(tmp_path), str(final), durations=[1.0, 1.0])
        assert final.read_bytes() == b"OLD-GOOD"

    def test_ffmpeg_format_is_explicit_because_extension_is_tmp(self, tmp_path):
        cmd = AudioAssembler()._build_ffmpeg_command(
            tmp_path / "l.txt", tmp_path / "x.mp3.tmp", 1.0, False)
        i = cmd.index("-f", cmd.index("-vn"))
        assert cmd[i + 1] == "mp3"
        assert cmd[-1].endswith("x.mp3.tmp")

    def test_concat_list_removed_on_failure(self, tmp_path):
        with patch("subprocess.Popen", side_effect=_fake_popen_writing([], returncode=1)), \
                patch("subprocess.run", return_value=MagicMock(stdout="1.0")):
            with pytest.raises(Exception):
                AudioAssembler().assemble_audio(_chunk_files(tmp_path), str(tmp_path / "f.mp3"),
                                                durations=[1.0, 1.0])
        assert not list(tmp_path.glob("concat_*.txt"))


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0,
                    reason="ffmpeg not installed")
class TestRealFfmpeg:
    def test_real_assembly_publishes_only_complete_file(self, tmp_path):
        src = tmp_path / "in.mp3"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.5",
                        "-c:a", "libmp3lame", str(src)], capture_output=True, check=True)
        final = tmp_path / "out.mp3"
        AudioAssembler().assemble_audio([str(src), str(src)], str(final), durations=[0.5, 0.5])
        assert final.exists() and final.stat().st_size > 0
        assert not (tmp_path / "out.mp3.tmp").exists()


# ---------------------------------------------------------------------------
# M5: temp dirs location
# ---------------------------------------------------------------------------

class TestPreparationWorkspace:
    def _worker(self, config):
        return PreparationWorker("tid", config, FileManager(), TextProcessor(), TextChunker())

    def _run(self, worker):
        result = {}
        worker.finished.connect(lambda chunks, t, a: result.update(text=t, audio=a, n=len(chunks)))
        worker.error.connect(lambda m: result.update(error=m))
        worker.run()
        return result

    def test_dirs_are_created_in_output_dir_not_input_dir(self, tmp_path):
        inp = tmp_path / "in"
        out = tmp_path / "out"
        inp.mkdir(), out.mkdir()
        (inp / "book.txt").write_text("Hello world. Second sentence.", encoding="utf-8")
        cfg = dataclasses.replace(make_config(out, "book"), input_file_path=str(inp / "book.txt"))
        result = self._run(self._worker(cfg))
        assert "error" not in result
        assert Path(result["text"]).parent == out
        assert Path(result["audio"]).parent == out
        assert list(inp.iterdir()) == [inp / "book.txt"]

    def test_read_only_input_directory_still_works(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        inp = tmp_path / "ro"
        out = tmp_path / "out"
        inp.mkdir(), out.mkdir()
        f = inp / "book.txt"
        f.write_text("Some text to read.", encoding="utf-8")
        inp.chmod(0o555)
        try:
            cfg = dataclasses.replace(make_config(out, "book"), input_file_path=str(f))
            result = self._run(self._worker(cfg))
        finally:
            inp.chmod(0o755)
        assert "error" not in result and result["n"] >= 1

    def test_unwritable_output_dir_falls_back_to_system_temp(self, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        out = tmp_path / "ro_out"
        out.mkdir()
        out.chmod(0o555)
        try:
            cfg = dataclasses.replace(make_config(tmp_path, "b"), output_dir_path=str(out))
            workspace = PreparationWorker.resolve_workspace_dir(cfg)
        finally:
            out.chmod(0o755)
        import tempfile
        assert workspace == tempfile.gettempdir()

    def test_missing_output_dir_falls_back_to_system_temp(self, tmp_path):
        cfg = MagicMock(output_dir_path=str(tmp_path / "does_not_exist"))
        import tempfile
        assert PreparationWorker.resolve_workspace_dir(cfg) == tempfile.gettempdir()

    def test_tilde_in_output_dir_is_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "Audio").mkdir()
        cfg = MagicMock(output_dir_path="~/Audio")
        assert PreparationWorker.resolve_workspace_dir(cfg) == str(tmp_path / "Audio")


# ---------------------------------------------------------------------------
# M6 (UI): error reason is visible in the backend panels
# ---------------------------------------------------------------------------

class TestStatusPanelsShowReason:
    def _selector(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from src.gui.widgets.tts_backend_selector import TtsBackendSelector
        xtts = MagicMock()
        xtts.is_model_loaded.return_value = False
        with patch.object(TtsBackendSelector, "_run_piper_check"), \
                patch.object(TtsBackendSelector, "_run_xtts_check"):
            return TtsBackendSelector(MagicMock(), xtts)

    def test_piper_error_message_is_shown(self):
        from src.application.services.piper_setup_service import PiperPrerequisiteResult
        sel = self._selector()
        sel._update_piper_status_display(PiperPrerequisiteResult(error_message="Docker daemon not running <b>"))
        assert not sel._status_error.isHidden()
        assert "Docker daemon not running" in sel._status_error.text()
        assert "<b>" not in sel._status_error.text()   # escaped

    def test_piper_error_hidden_when_ok(self):
        from src.application.services.piper_setup_service import PiperPrerequisiteResult
        sel = self._selector()
        sel._update_piper_status_display(PiperPrerequisiteResult(error_message="x"))
        sel._update_piper_status_display(PiperPrerequisiteResult(docker_available=True, image_available=True,
                                                                 model_present=True, container_running=True))
        assert sel._status_error.isHidden()

    def test_xtts_error_message_is_shown_and_cleared(self):
        from src.infrastructure.xtts_model_manager import XttsDiagnosticsResult
        sel = self._selector()
        sel._update_xtts_status_display(XttsDiagnosticsResult(error_message="CUDA not available"))
        assert not sel._xtts_status_error.isHidden()
        assert "CUDA not available" in sel._xtts_status_error.text()
        sel._update_xtts_status_display(XttsDiagnosticsResult(
            tts_package_installed=True, cuda_available=True, model_cached=True))
        assert sel._xtts_status_error.isHidden()

    def test_rechecking_clears_previous_reason(self):
        from src.application.services.piper_setup_service import PiperPrerequisiteResult
        sel = self._selector()
        sel._update_piper_status_display(PiperPrerequisiteResult(error_message="old"))
        sel._set_piper_checking_state()
        assert sel._status_error.isHidden()
