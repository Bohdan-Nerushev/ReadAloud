"""
End-to-end scenarios for Piper (local neural TTS in Docker, Wyoming protocol).

The real application runs, including the real Wyoming client talking over a real TCP socket to
``FakeWyomingServer``; only the Docker CLI layer (``FakeDocker``) and downloads are faked.
"""

import pytest

from src.domain.models import TaskStatus, TtsBackend
from src.domain.piper_audio_generator import PIPER_VOICE_MAPPING
from tests.e2e.rig import dominant_freqs, freq_for, mp3_duration, sample_text, silent_fraction

pytestmark = pytest.mark.e2e

PIPER = TtsBackend.PIPER
LANGS = {"Ukrainian": "uk", "English": "en", "German": "de", "Russian": "ru",
         "French": "fr", "Spanish": "es", "Italian": "it"}


def pitch(path) -> float:
    freqs = [f for f in dominant_freqs(path) if f > 50]
    assert freqs
    return sum(freqs) / len(freqs)


def selected_piper(rig):
    rig.select_backend(PIPER)
    rig.wait_backend_panel(PIPER)
    return rig.window.tts_backend_selector


class TestPanel:
    def test_panel_reflects_a_ready_environment_without_starting_anything(self, rig):
        sel = selected_piper(rig)
        assert sel._piper_panel.isVisible() and not sel._xtts_panel.isVisible()
        assert "✅" in sel._status_docker.text()
        assert "✅" in sel._status_image.text()
        assert "✅" in sel._status_model.text()
        assert rig.docker.up_calls == [] and not rig.server.running

    def test_docker_missing_is_shown_in_the_panel(self, make_rig):
        rig = make_rig()
        rig.docker.docker_available = False
        sel = selected_piper(rig)
        assert "❌" in sel._status_docker.text()

    def test_language_change_rechecks_the_voice_for_the_new_language(self, rig):
        sel = selected_piper(rig)
        for f in rig.piper_models_dir.glob("de_DE*"):
            f.unlink()
        rig.set_language("German")
        rig.wait_for(lambda: "❌" in sel._status_model.text(), what="model status for German")
        rig.set_language("English")
        rig.wait_for(lambda: "✅" in sel._status_model.text(), what="model status for English")


class TestVoices:
    @pytest.mark.parametrize("language", list(LANGS))
    @pytest.mark.parametrize("gender", ["Male", "Female"])
    def test_container_and_requests_use_the_voice_mapped_to_language_and_gender(self, rig, language, gender):
        code = LANGS[language]
        voice = PIPER_VOICE_MAPPING[code][gender.lower()]
        rig.submit(rig.write_text(text=sample_text(900)), PIPER, language=language, gender=gender)
        rig.wait_idle()
        assert len(rig.final_files()) == 1
        assert rig.docker.up_calls[0]["PIPER_VOICE"] == voice
        assert {r["voice"] for r in rig.server.requests} == {voice}
        assert pitch(rig.final_files()[0]) == pytest.approx(freq_for(voice), abs=25)

    def test_male_and_female_english_voices_sound_different(self, rig):
        path = rig.write_text(text=sample_text(900))
        rig.submit(path, PIPER, gender="Male", name="m")
        rig.wait_idle()
        rig.submit(path, PIPER, gender="Female", name="f")
        rig.wait_idle()
        pitches = {f.name.split("_")[0]: pitch(f) for f in rig.final_files()}
        assert abs(pitches["m"] - pitches["f"]) > 20

    def test_a_task_for_another_voice_is_served_by_the_running_container(self, rig):
        rig.submit(rig.write_text("a.txt", sample_text(900, "Alpha")), PIPER, gender="Male", name="a")
        rig.submit(rig.write_text("b.txt", sample_text(900, "Beta")), PIPER, gender="Female", name="b")
        rig.wait_idle()
        voices = {r["voice"] for r in rig.server.requests}
        assert voices == {PIPER_VOICE_MAPPING["en"]["male"], PIPER_VOICE_MAPPING["en"]["female"]}
        assert len(rig.final_files()) == 2


class TestContainerLifecycle:
    def test_container_is_started_for_the_task_and_stopped_afterwards(self, rig):
        rig.submit(rig.write_text(), PIPER)
        rig.wait_idle()
        assert len(rig.docker.up_calls) == 1
        rig.wait_for(lambda: rig.docker.down_calls == 1, what="container to be stopped")
        assert not rig.server.running

    def test_one_container_serves_the_whole_queue(self, rig):
        for i in range(3):
            rig.submit(rig.write_text(f"f{i}.txt", sample_text(600, f"F{i}")), PIPER, name=f"f{i}")
        rig.wait_idle(timeout=90)
        assert len(rig.docker.up_calls) == 1
        rig.wait_for(lambda: rig.docker.down_calls == 1, what="container to be stopped")
        assert len(rig.final_files()) == 3

    def test_already_running_container_is_reused(self, rig):
        rig.server.start()
        rig.submit(rig.write_text(), PIPER)
        rig.wait_idle()
        assert rig.docker.up_calls == []
        assert len(rig.final_files()) == 1

    def test_missing_image_is_pulled_automatically(self, rig):
        rig.docker.image_available = False
        rig.submit(rig.write_text(), PIPER)
        rig.wait_idle()
        assert rig.docker.pull_calls == 1
        assert len(rig.final_files()) == 1

    def test_missing_voice_model_is_downloaded_automatically(self, make_rig, monkeypatch):
        rig = make_rig(piper_models=False)
        fetched = []

        def fake_retrieve(url, dest, *a, **k):
            fetched.append(url)
            open(dest, "wb").write(b"model")

        monkeypatch.setattr("urllib.request.urlretrieve", fake_retrieve)
        rig.submit(rig.write_text(), PIPER)
        rig.wait_idle()
        voice = PIPER_VOICE_MAPPING["en"]["male"]
        assert any(voice in u and u.endswith(".onnx") for u in fetched)
        assert rig.piper_models.is_model_present(voice)
        assert len(rig.final_files()) == 1

    def test_failed_model_download_fails_fast_with_the_reason(self, make_rig, monkeypatch):
        rig = make_rig(piper_models=False)

        def boom(*a, **k):
            raise OSError("Network is unreachable")

        monkeypatch.setattr("urllib.request.urlretrieve", boom)
        rig.submit(rig.write_text(), PIPER)
        rig.wait_idle()
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("voice model" in t.lower() and "unreachable" in t.lower() for t in rig.dialogs.texts())
        assert rig.server.requests == [] and rig.docker.up_calls == []

    def test_docker_unavailable_fails_fast_with_the_reason(self, rig):
        rig.docker.docker_available = False
        rig.submit(rig.write_text(), PIPER)
        rig.wait_idle(timeout=20)
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("Piper (Docker) is not ready" in t for t in rig.dialogs.texts())
        assert rig.server.requests == []

    def test_compose_failure_fails_fast_and_nothing_is_sent_to_a_dead_server(self, rig):
        rig.docker.compose_error = RuntimeError("compose up failed: no space left on device")
        rig.submit(rig.write_text(), PIPER)
        rig.wait_idle(timeout=20)
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("no space left" in t for t in rig.dialogs.texts())
        assert rig.server.requests == []

    def test_port_occupied_by_a_foreign_process_is_reported(self, rig):
        import socket
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", rig.server.port))
        blocker.listen(128)
        rig.docker.foreign_listener = blocker
        try:
            rig.submit(rig.write_text(), PIPER)
            rig.wait_idle(timeout=20)
        finally:
            blocker.close()
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("port" in t.lower() for t in rig.dialogs.texts()), rig.dialogs.messages
        assert rig.docker.up_calls == []

    def test_failed_task_still_stops_the_container_it_started(self, rig):
        rig.fast_retries()
        rig.break_engine(PIPER)
        rig.submit(rig.write_text(text=sample_text(1200)), PIPER)
        rig.wait_idle(timeout=90)
        assert rig.tasks()[0].status == TaskStatus.FAILED
        rig.wait_for(lambda: rig.docker.down_calls == 1, what="container stop after a failure")

    def test_unsupported_language_never_reaches_docker(self, rig, monkeypatch):
        monkeypatch.setattr(rig.piper_models, "is_language_supported", lambda lang: False)
        rig.submit(rig.write_text(), PIPER)
        rig.wait_idle(timeout=20)
        assert rig.tasks()[0].status == TaskStatus.FAILED
        assert any("not supported by Piper" in t for t in rig.dialogs.texts())
        assert rig.docker.up_calls == []


class TestStreamingRobustness:
    def test_truncated_stream_is_retried_and_the_result_is_complete(self, rig):
        rig.fast_retries()
        text = sample_text(2400)
        rig.server.behaviours = ["truncate", "truncate"]
        rig.submit(rig.write_text(text=text), PIPER)
        rig.wait_idle(timeout=90)
        (final,) = rig.final_files()
        assert mp3_duration(final) == pytest.approx(len(text) * 0.008, rel=0.3)
        assert silent_fraction(final) < 0.15
        assert len(rig.server.requests) > 2

    def test_connection_dropped_before_audio_is_retried(self, rig):
        rig.fast_retries()
        rig.server.behaviours = ["drop"]
        rig.submit(rig.write_text(text=sample_text(1200)), PIPER)
        rig.wait_idle(timeout=90)
        assert len(rig.final_files()) == 1
        assert rig.dialogs.messages == []

    def test_requests_are_never_sent_in_parallel(self, rig):
        rig.window.thread_selector.show()
        rig.set_threads(4)
        rig.select_backend(PIPER)
        rig.submit(rig.write_text(text=sample_text(6000)), PIPER)
        rig.wait_idle(timeout=90)
        assert len(rig.server.requests) > 3
        assert rig.server.max_active == 1
