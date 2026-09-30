"""
Scenarios that mix engines in one queue and check that every UI control reflects the real state.
"""

import threading

import pytest

from src.domain.models import TaskStatus, TtsBackend
from tests.e2e.rig import mp3_duration, sample_text

pytestmark = pytest.mark.e2e

EDGE, PIPER, XTTS = TtsBackend.EDGE_TTS, TtsBackend.PIPER, TtsBackend.XTTS


class TestMixedQueue:
    def test_each_task_goes_through_its_own_engine_and_resources_are_freed_at_the_end(self, rig):
        rig.submit(rig.write_text("e.txt", sample_text(900, "Edgy")), EDGE, name="edge")
        rig.submit(rig.write_text("p.txt", sample_text(900, "Piperish")), PIPER, name="piper")
        rig.submit(rig.write_text("x.txt", sample_text(900, "Xttsish")), XTTS, name="xtts")
        rig.wait_idle(timeout=120)

        assert sorted(f.name.split("_")[0] for f in rig.final_files()) == ["edge", "piper", "xtts"]
        assert all("Edgy" in t for t in rig.engine_calls(EDGE))
        assert all("Piperish" in t for t in rig.engine_calls(PIPER))
        assert all("Xttsish" in t for t in rig.engine_calls(XTTS))
        assert rig.engine_calls(EDGE) and rig.engine_calls(PIPER) and rig.engine_calls(XTTS)

        rig.wait_for(lambda: rig.docker.down_calls == 1, what="Piper container stop")
        rig.wait_for(lambda: rig.gpu.allocated() == 0, what="XTTS VRAM release")
        assert len(rig.docker.up_calls) == 1 and rig.gpu.loads == 1

    def test_a_failing_engine_does_not_stop_the_other_engines_tasks(self, rig):
        rig.docker.docker_available = False
        rig.submit(rig.write_text("p.txt", sample_text(600, "Piperish")), PIPER, name="piper")
        rig.submit(rig.write_text("x.txt", sample_text(600, "Xttsish")), XTTS, name="xtts")
        rig.submit(rig.write_text("e.txt", sample_text(600, "Edgy")), EDGE, name="edge")
        rig.wait_idle(timeout=90)
        assert sorted(f.name.split("_")[0] for f in rig.final_files()) == ["edge", "xtts"]
        assert [t.config.tts_backend for t in rig.tasks()] == [PIPER]
        assert rig.tasks()[0].status == TaskStatus.FAILED

    def test_switching_engine_between_additions_keeps_each_tasks_settings(self, rig):
        rig.submit(rig.write_text("a.txt", sample_text(600)), XTTS, language="German", gender="Female", name="a")
        rig.submit(rig.write_text("b.txt", sample_text(600)), PIPER, language="Russian", gender="Male", name="b")
        configs = {t.config.project_name: t.config for t in rig.tasks()}
        assert (configs["a"].tts_backend, configs["a"].language, configs["a"].gender) == (XTTS, "de", "female")
        assert (configs["b"].tts_backend, configs["b"].language, configs["b"].gender) == (PIPER, "ru", "male")
        rig.wait_idle(timeout=90)
        assert len(rig.final_files()) == 2


class TestBackendSelectorUi:
    def test_panels_and_thread_selector_follow_the_selected_engine(self, rig):
        w = rig.window
        sel = w.tts_backend_selector
        assert sel._edge_radio.isChecked()
        assert w.thread_selector.isVisible()
        assert not sel._piper_panel.isVisible() and not sel._xtts_panel.isVisible()

        rig.select_backend(PIPER)
        assert sel._piper_panel.isVisible() and not sel._xtts_panel.isVisible()
        assert not w.thread_selector.isVisible()

        rig.select_backend(XTTS)
        assert sel._xtts_panel.isVisible() and not sel._piper_panel.isVisible()
        assert not w.thread_selector.isVisible()

        rig.select_backend(EDGE)
        assert w.thread_selector.isVisible()
        assert not sel._piper_panel.isVisible() and not sel._xtts_panel.isVisible()

    def test_radio_buttons_are_mutually_exclusive(self, rig):
        sel = rig.window.tts_backend_selector
        for backend, radio in ((PIPER, sel._piper_radio), (XTTS, sel._xtts_radio), (EDGE, sel._edge_radio)):
            rig.select_backend(backend)
            checked = [r for r in (sel._edge_radio, sel._piper_radio, sel._xtts_radio) if r.isChecked()]
            assert checked == [radio]


class TestFormUi:
    def test_initial_state_is_ready_for_input(self, rig):
        w = rig.window
        assert w.control_buttons._start_button.isEnabled()
        assert w.progress_display.isHidden()
        assert w.file_selector.get_selected_files() == []
        assert rig.tasks() == []

    def test_project_name_is_suggested_from_the_file_and_kept_when_the_user_edits_it(self, rig):
        rig.choose_files(rig.write_text("My Story.txt", "Hello there."))
        assert rig.window.project_input.get_project_name() == "My Story"
        rig.set_project_name("Custom")
        rig.choose_files(rig.write_text("Other.txt", "Hello there."))
        assert rig.window.project_input.get_project_name() == "Custom"

    def test_form_is_cleared_after_adding_and_ready_for_the_next_file(self, rig):
        rig.submit(rig.write_text(), EDGE)
        assert rig.window.file_selector.get_selected_files() == []
        rig.wait_idle()
        rig.submit(rig.write_text("two.txt", "Second book."), EDGE, name="two")
        rig.wait_idle()
        assert len(rig.final_files()) == 2

    def test_add_button_stays_usable_while_the_queue_is_running(self, rig):
        gate = threading.Event()
        rig.edge.gate = gate
        rig.submit(rig.write_text(text=sample_text(2400)), EDGE, name="first")
        rig.wait_for(lambda: rig.edge.calls, what="first task to start")
        assert rig.window.control_buttons._start_button.isEnabled()
        rig.submit(rig.write_text("two.txt", "Second in line."), EDGE, name="second")
        assert len(rig.tasks()) == 2
        gate.set()
        rig.wait_idle(timeout=60)
        assert len(rig.final_files()) == 2

    def test_progress_widget_is_shown_while_running_and_hidden_afterwards(self, rig):
        gate = threading.Event()
        rig.edge.gate = gate
        rig.submit(rig.write_text(text=sample_text(2400)), EDGE)
        rig.wait_for(lambda: rig.edge.calls, what="generation to start")
        assert not rig.window.progress_display.isHidden()
        gate.set()
        rig.wait_idle()
        assert rig.window.progress_display.isHidden()

    def test_queue_item_buttons_reflect_the_task_state(self, rig):
        gate = threading.Event()
        rig.edge.gate = gate
        rig.submit(rig.write_text(text=sample_text(2400)), EDGE)
        rig.wait_for(lambda: rig.edge.calls, what="generation to start")
        (task,) = rig.tasks()
        assert rig.queue_button(task, "pause").text() == "Pause"
        rig.queue_button(task, "pause").click()
        rig.pump(0.1)
        assert rig.queue_button(task, "pause").text() == "Resume"
        rig.queue_button(task, "pause").click()
        rig.pump(0.1)
        assert rig.queue_button(task, "pause").text() == "Pause"
        gate.set()
        rig.wait_idle()

    def test_removing_a_failed_task_clears_it_and_its_temporary_files(self, rig):
        rig.submit(rig.write_text("empty.txt", ""), EDGE)
        rig.wait_idle()
        (task,) = rig.tasks()
        rig.queue_button(task, "delete").click()
        rig.pump(0.3)
        assert rig.tasks() == []
        assert rig.window.queue_list._items == {}
        assert rig.leftover_work_dirs() == []

    def test_final_audio_is_a_playable_mp3_with_sane_length(self, rig):
        rig.submit(rig.write_text(text=sample_text(2400)), EDGE)
        rig.wait_idle()
        assert 10 < mp3_duration(rig.final_files()[0]) < 40
