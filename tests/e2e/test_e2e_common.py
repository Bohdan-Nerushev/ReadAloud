"""
End-to-end user scenarios that every TTS engine must satisfy, driven through the real
window: Edge TTS (cloud), Piper (Docker/Wyoming) and XTTS-v2 (local GPU).
"""

import re
import threading
import time

import pytest

from src.domain.models import TaskStatus, TtsBackend
from tests.e2e.rig import SECONDS_PER_CHAR, mp3_duration, sample_text, silent_fraction

pytestmark = pytest.mark.e2e

ENGINES = [
    pytest.param(TtsBackend.EDGE_TTS, id="edge"),
    pytest.param(TtsBackend.PIPER, id="piper"),
    pytest.param(TtsBackend.XTTS, id="xtts"),
]


def normalized(texts):
    return re.sub(r"\s+", " ", " ".join(texts))


def assert_sentences_in_order(spoken: str, count: int, prefix: str = "Sentence") -> None:
    """Every numbered sentence reached the engine, once, in book order."""
    last = -1
    for i in range(1, count + 1):
        pos = spoken.find(f"{prefix} number {i} ")
        assert pos != -1, f"sentence {i} never reached the engine"
        assert pos > last, f"sentence {i} is out of order"
        last = pos


def expected_seconds(text: str) -> float:
    return len(text) * SECONDS_PER_CHAR


@pytest.mark.parametrize("backend", ENGINES)
class TestHappyPath:
    def test_single_file_produces_a_valid_mp3_and_a_clean_ui(self, rig, backend):
        text = sample_text(2400)
        rig.submit(rig.write_text(text=text), backend)
        rig.wait_idle()

        files = rig.final_files()
        assert len(files) == 1
        assert files[0].name.startswith("book_") and files[0].suffix == ".mp3"
        duration = mp3_duration(files[0])
        assert expected_seconds(text) * 0.75 < duration < expected_seconds(text) * 1.6
        assert silent_fraction(files[0]) < 0.2
        assert rig.dialogs.messages == []
        assert rig.tasks() == []
        assert rig.window.queue_list._items == {}
        assert rig.window.control_buttons._start_button.isEnabled()
        assert rig.window.progress_display.isHidden()
        assert rig.leftover_work_dirs() == []
        assert not list((rig.out / "final").glob("*.tmp"))
        assert len(list((rig.out / "final").iterdir())) == 1

    def test_whole_text_reaches_the_engine_in_order(self, rig, backend):
        rig.submit(rig.write_text(text=sample_text(4200)), backend)
        rig.wait_idle()
        spoken = normalized(rig.engine_calls(backend))
        last_number = int(re.findall(r"number (\d+) ", sample_text(4200))[-1])
        assert_sentences_in_order(spoken, last_number)

    def test_speed_shortens_the_final_audio(self, rig, backend):
        text = sample_text(1800)
        path = rig.write_text(text=text)
        rig.submit(path, backend, speed="1.0", name="normal")
        rig.wait_idle()
        rig.submit(path, backend, speed="1.5", name="fast")
        rig.wait_idle()
        durations = {f.name.split("_")[0]: mp3_duration(f) for f in rig.final_files()}
        assert set(durations) == {"normal", "fast"}
        assert durations["normal"] / durations["fast"] == pytest.approx(1.5, rel=0.12)

    def test_custom_project_name_names_the_output(self, rig, backend):
        rig.submit(rig.write_text(text="A short story."), backend, name="My Audio Book")
        rig.wait_idle()
        assert [f.name.startswith("My Audio Book_") for f in rig.final_files()] == [True]

    def test_unicode_text_and_filename(self, rig, backend):
        text = "Їжак п'ять разів сказав: «Привіт, світе!» Це ґанок біля річки. " * 3
        path = rig.write_text("Книга про п'ять річок.txt", text)
        rig.submit(path, backend, language="Ukrainian", gender="Female")
        rig.wait_idle()
        assert len(rig.final_files()) == 1
        assert rig.dialogs.messages == []
        assert "п'ять" in " ".join(rig.engine_calls(backend))

    def test_large_book_finishes_with_every_chunk_in_place(self, rig, backend):
        text = sample_text(12000)
        rig.submit(rig.write_text(text=text), backend)
        rig.wait_idle(timeout=90)
        (final,) = rig.final_files()
        assert mp3_duration(final) == pytest.approx(expected_seconds(text), rel=0.4)
        assert silent_fraction(final) < 0.2
        numbers = int(re.findall(r"number (\d+) ", text)[-1])
        assert_sentences_in_order(normalized(rig.engine_calls(backend)), numbers)


@pytest.mark.parametrize("backend", ENGINES)
class TestQueueAndBatch:
    def test_several_files_selected_at_once_become_separate_mp3s(self, rig, backend):
        files = [rig.write_text(f"part{i}.txt", sample_text(900, f"Part{i}")) for i in (1, 2, 3)]
        rig.choose_files(*files)
        rig.choose_output()
        rig.select_backend(backend)
        rig.click_add()
        assert len(rig.tasks()) == 3
        rig.wait_idle(timeout=90)
        names = sorted(f.name.split("_")[0] for f in rig.final_files())
        assert names == ["part1", "part2", "part3"]
        assert rig.window.file_selector.get_selected_files() == []
        assert rig.dialogs.messages == []

    def test_equally_named_files_from_different_folders_do_not_overwrite(self, rig, backend):
        a = rig.write_text("chapter.txt", sample_text(600, "Alpha"), rig.inputs / "a")
        b = rig.write_text("chapter.txt", sample_text(600, "Beta"), rig.inputs / "b")
        rig.choose_files(a, b)
        rig.choose_output()
        rig.select_backend(backend)
        rig.click_add()
        rig.wait_idle(timeout=90)
        names = sorted(f.name for f in rig.final_files())
        assert len(names) == 2 and len(set(names)) == 2
        assert {n.split("_")[0] for n in names} == {"chapter"} or {n.rsplit("_", 2)[0] for n in names}

    def test_tasks_run_one_after_another_in_queue_order(self, rig, backend):
        first = rig.write_text("first.txt", sample_text(900, "First"))
        second = rig.write_text("second.txt", sample_text(900, "Second"))
        rig.submit(first, backend, name="first")
        rig.submit(second, backend, name="second")
        assert len(rig.tasks()) == 2
        rig.wait_idle(timeout=90)
        spoken = normalized(rig.engine_calls(backend))
        assert spoken.find("First number 1 ") < spoken.find("Second number 1 ")
        assert len(rig.final_files()) == 2


@pytest.mark.parametrize("backend", ENGINES)
class TestValidationAndInputErrors:
    def test_adding_without_a_file_warns_and_queues_nothing(self, rig, backend):
        rig.select_backend(backend)
        rig.click_add()
        assert rig.tasks() == []
        assert any("cannot be empty" in t for t in rig.dialogs.texts("warning"))

    def test_invalid_project_name_is_rejected(self, rig, backend):
        rig.submit(rig.write_text(text="Hello there."), backend, name="bad:name?")
        assert rig.tasks() == []
        assert rig.dialogs.texts("warning")

    def test_empty_file_fails_clearly_without_touching_the_engine(self, rig, backend):
        rig.submit(rig.write_text("empty.txt", ""), backend)
        rig.wait_idle()
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("No text chunks" in t for t in rig.dialogs.texts())
        assert rig.engine_calls(backend) == []
        assert rig.final_files() == []
        assert rig.queue_widget(task).pause_button.text() == "Retry"

    def test_file_deleted_before_adding_is_reported_and_not_queued(self, rig, backend):
        path = rig.write_text("gone.txt", "Some words to say.")
        rig.choose_files(path)
        path.unlink()
        rig.choose_output()
        rig.select_backend(backend)
        rig.click_add()
        rig.wait_idle()
        assert rig.dialogs.messages, "the user must be told about the missing file"
        assert rig.final_files() == []
        assert rig.engine_calls(backend) == []


@pytest.mark.parametrize("backend", ENGINES)
class TestCancelPauseRetry:
    def test_cancel_running_task_stops_work_cleans_up_and_app_recovers(self, rig, backend):
        gate = rig.engine_gate(backend)
        rig.submit(rig.write_text(text=sample_text(3000)), backend)
        rig.wait_for(lambda: rig.engine_calls(backend), what="engine to start synthesising")
        (task,) = rig.tasks()

        threading.Timer(4.0, gate.set).start()  # safety net: never hang the test run
        started = time.monotonic()
        rig.queue_button(task, "delete").click()
        elapsed = time.monotonic() - started
        assert elapsed < 2.0, "cancelling must not block the GUI thread on the running batch"
        gate.set()
        rig.pump(0.5)

        assert rig.tasks() == []
        assert rig.window.queue_list._items == {}
        rig.wait_for(lambda: rig.leftover_work_dirs() == [], what="temporary directories to be removed")
        assert rig.final_files() == []

        rig.edge.gate = rig.server.gate = rig.gpu.gate = None
        rig.submit(rig.write_text("again.txt", "Fresh start after the cancel."), backend, name="again")
        rig.wait_idle()
        assert [f.name.startswith("again_") for f in rig.final_files()] == [True]

    def test_pause_holds_the_work_and_resume_finishes_it(self, rig, backend):
        gate = rig.engine_gate(backend)
        rig.submit(rig.write_text(text=sample_text(4000)), backend)
        rig.wait_for(lambda: rig.engine_calls(backend), what="engine to start")
        (task,) = rig.tasks()

        rig.queue_button(task, "pause").click()
        rig.pump(0.05)
        assert task.status == TaskStatus.PAUSED
        assert rig.queue_button(task, "pause").text() == "Resume"

        gate.set()
        rig.pump(0.8)
        calls_when_settled = len(rig.engine_calls(backend))
        rig.pump(1.0)
        assert len(rig.engine_calls(backend)) == calls_when_settled, "paused task kept synthesising"
        assert task.status == TaskStatus.PAUSED
        assert rig.final_files() == []

        rig.queue_button(task, "pause").click()
        rig.wait_idle()
        assert len(rig.final_files()) == 1
        assert rig.dialogs.messages == []

    def test_failure_is_reported_and_retry_button_recovers(self, rig, backend):
        rig.fast_retries()
        rig.break_engine(backend)
        rig.submit(rig.write_text(text=sample_text(2400)), backend)
        rig.wait_idle(timeout=90)

        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert rig.dialogs.texts(), "the failure must be shown to the user"
        assert rig.final_files() == [], "a failed task must not leave a (partial) final MP3"
        assert rig.queue_button(task, "pause").text() == "Retry"
        assert rig.window.control_buttons._start_button.isEnabled()

        rig.heal_engine(backend)
        rig.queue_button(task, "pause").click()
        rig.wait_idle(timeout=90)
        assert len(rig.final_files()) == 1
        assert mp3_duration(rig.final_files()[0]) > 5
        assert rig.tasks() == []

    def test_failed_task_does_not_block_the_next_one(self, rig, backend):
        rig.fast_retries()
        rig.submit(rig.write_text("bad.txt", ""), backend, name="bad")
        rig.submit(rig.write_text("good.txt", "A perfectly fine sentence."), backend, name="good")
        rig.wait_idle()
        assert [f.name.startswith("good_") for f in rig.final_files()] == [True]
        assert [t.status for t in rig.tasks()] == [TaskStatus.FAILED]


@pytest.mark.parametrize("backend", ENGINES)
class TestRestart:
    def test_queue_survives_a_restart_with_the_same_engine(self, make_rig, backend):
        rig = make_rig()
        gate = rig.engine_gate(backend)
        rig.submit(rig.write_text(text=sample_text(2400)), backend, language="Ukrainian",
                   gender="Female", name="persisted")
        rig.wait_for(lambda: rig.engine_calls(backend), what="engine to start")
        workdir = rig.workdir
        threading.Timer(3.0, gate.set).start()
        rig.close()
        gate.set()

        again = make_rig(workdir=workdir)
        (task,) = again.tasks()
        assert task.config.tts_backend == backend
        assert (task.config.language, task.config.gender) == ("uk", "female")
        again.wait_for(lambda: again.engine_calls(backend) or task.status == TaskStatus.PAUSED,
                       what="restored task to start or wait for resume")
        if task.status == TaskStatus.PAUSED:
            again.queue_button(task, "pause").click()
        again.wait_idle(timeout=90)

        assert len(again.final_files()) == 1
        for other in (b for b in (TtsBackend.EDGE_TTS, TtsBackend.PIPER, TtsBackend.XTTS) if b != backend):
            assert again.engine_calls(other) == [], f"restored {backend.value} task went through {other.value}"
