"""
End-to-end scenarios for XTTS-v2 (local GPU, voice cloning).

The real application runs (window, controller, queue, generator, model manager, ffmpeg
assembly). Only CUDA/torch and the ``TTS`` package are faked, by ``tests.e2e.rig.FakeGpu``:
the fake model "clones" the dominant frequency of the reference WAV, so the pitch of the final
MP3 proves which reference voice was actually used.
"""

import threading
import time

import pytest

from src.domain.models import TaskStatus, TtsBackend
from tests.e2e.rig import GB, dominant_freqs, mp3_duration, sample_text, silent_fraction, write_tone_wav

pytestmark = pytest.mark.e2e

XTTS = TtsBackend.XTTS


def pitch(path) -> float:
    freqs = [f for f in dominant_freqs(path) if f > 50]
    assert freqs, "no audible signal in the MP3"
    return sum(freqs) / len(freqs)


def selected_xtts(rig):
    rig.select_backend(XTTS)
    rig.wait_backend_panel(XTTS)
    return rig.window.tts_backend_selector


def label(widget) -> str:
    return widget.text()


class TestPanel:
    def test_ready_environment_shows_everything_green_and_nothing_loaded(self, rig):
        sel = selected_xtts(rig)
        assert sel._xtts_panel.isVisible()
        assert not sel._piper_panel.isVisible()
        for widget in (sel._xtts_status_package, sel._xtts_status_cuda, sel._xtts_status_vram,
                       sel._xtts_status_model):
            assert "✅" in label(widget), label(widget)
        assert "not loaded" in label(sel._xtts_status_loaded)
        assert not sel._btn_xtts_unload.isEnabled()
        assert not sel._btn_xtts_download.isEnabled()
        assert sel._btn_xtts_recheck.isEnabled()
        assert rig.gpu.loads == 0, "merely selecting XTTS must not load the model"

    def test_thread_selector_is_hidden_for_a_local_engine(self, rig):
        assert rig.window.thread_selector.isVisible()
        rig.select_backend(XTTS)
        assert not rig.window.thread_selector.isVisible()
        rig.select_backend(TtsBackend.EDGE_TTS)
        assert rig.window.thread_selector.isVisible()

    def test_no_cuda_is_reported_and_blocks_the_task(self, make_rig):
        rig = make_rig()
        rig.gpu.cuda = False
        sel = selected_xtts(rig)
        assert "❌" in label(sel._xtts_status_cuda)
        rig.submit(rig.write_text(), XTTS)
        rig.wait_idle()
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("XTTS-v2 is not ready" in t and "CUDA" in t for t in rig.dialogs.texts())
        assert rig.gpu.loads == 0 and rig.gpu.inference_calls == []

    def test_insufficient_vram_blocks_the_task_with_the_reason(self, make_rig):
        rig = make_rig(total_vram_gb=3.0)
        rig.submit(rig.write_text(), XTTS)
        rig.wait_idle()
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("VRAM" in t for t in rig.dialogs.texts())
        assert rig.gpu.loads == 0

    def test_missing_tts_package_blocks_the_task(self, make_rig, monkeypatch):
        rig = make_rig()
        import importlib.util
        real = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, *a, **k: None if name == "TTS" else real(name, *a, **k))
        sel = selected_xtts(rig)
        assert "❌" in label(sel._xtts_status_package)
        rig.submit(rig.write_text(), XTTS)
        rig.wait_idle()
        assert rig.tasks()[0].status == TaskStatus.FAILED
        assert any("not ready" in t for t in rig.dialogs.texts())

    def test_missing_model_can_be_downloaded_from_the_ui_and_then_used(self, make_rig):
        rig = make_rig(model_cached=False)
        sel = selected_xtts(rig)
        assert "❌" in label(sel._xtts_status_model) or "not" in label(sel._xtts_status_model).lower()
        assert sel._btn_xtts_download.isEnabled()

        rig.submit(rig.write_text(), XTTS)
        rig.wait_idle()
        assert rig.tasks()[0].status == TaskStatus.FAILED, "an uncached model must fail fast"
        assert rig.gpu.inference_calls == []
        rig.tasks()[0]  # stays in history

        sel._btn_xtts_download.click()
        rig.wait_for(lambda: rig.gpu.downloads == 1, what="model download")
        rig.wait_for(lambda: "✅" in label(sel._xtts_status_model), what="model cache check")
        assert not sel._btn_xtts_download.isEnabled()

        rig.queue_button(rig.tasks()[0], "pause").click()  # Retry
        rig.wait_idle()
        assert len(rig.final_files()) == 1

    def test_missing_reference_voice_fails_fast_and_names_the_file(self, make_rig):
        rig = make_rig(with_speakers=False)
        rig.submit(rig.write_text(), XTTS, language="Ukrainian", gender="Female")
        rig.wait_idle()
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("uk" in t and "female" in t.lower() for t in rig.dialogs.texts()), rig.dialogs.texts()
        assert rig.gpu.inference_calls == []
        assert rig.final_files() == []

    def test_recheck_button_picks_up_a_fixed_environment(self, make_rig):
        rig = make_rig(with_speakers=False)
        sel = selected_xtts(rig)
        assert "✅" not in label(sel._xtts_status_speakers)
        rig.write_speakers()
        sel._btn_xtts_recheck.click()
        rig.wait_for(lambda: "✅" in label(sel._xtts_status_speakers), what="speaker status")


class TestVoiceCloning:
    @pytest.mark.parametrize("language,code,gender", [
        ("Ukrainian", "uk", "Female"),
        ("Ukrainian", "uk", "Male"),
        ("English", "en", "Male"),
        ("English", "en", "Female"),
        ("German", "de", "Female"),
        ("Russian", "ru", "Male"),
    ])
    def test_final_mp3_carries_the_chosen_reference_voice(self, rig, language, code, gender):
        rig.submit(rig.write_text(text=sample_text(1800)), XTTS, language=language, gender=gender)
        rig.wait_idle()
        (final,) = rig.final_files()
        expected = rig.speaker_freq(code, gender.lower())
        assert pitch(final) == pytest.approx(expected, abs=25)
        assert {c["language"] for c in rig.gpu.inference_calls} == {code}
        assert silent_fraction(final) < 0.15

    def test_replacing_the_reference_recording_changes_the_voice(self, rig):
        path = rig.write_text(text=sample_text(1200))
        rig.submit(path, XTTS, language="English", gender="Female", name="before")
        rig.wait_idle()
        write_tone_wav(rig.speakers_dir / "en" / "female.wav", 1500.0)
        rig.submit(path, XTTS, language="English", gender="Female", name="after")
        rig.wait_idle()
        by_name = {f.name.split("_")[0]: f for f in rig.final_files()}
        assert pitch(by_name["before"]) == pytest.approx(600, abs=25)
        assert pitch(by_name["after"]) == pytest.approx(1500, abs=40)

    def test_every_segment_uses_the_same_cloned_voice(self, rig):
        rig.submit(rig.write_text(text=sample_text(6000)), XTTS, language="German", gender="Male")
        rig.wait_idle()
        assert len(rig.gpu.inference_calls) > 3
        assert {round(c["freq"]) for c in rig.gpu.inference_calls} == {700}

    def test_reference_is_analysed_once_per_task_not_per_segment(self, rig):
        rig.submit(rig.write_text(text=sample_text(6000)), XTTS)
        rig.wait_idle()
        assert len(rig.gpu.inference_calls) > 3
        assert len(rig.gpu.latent_requests) == 1

    @pytest.mark.parametrize("language", ["Spanish", "French", "Italian"])
    def test_languages_xtts_cannot_clone_fail_fast_with_a_clear_reason(self, rig, language):
        rig.submit(rig.write_text(), XTTS, language=language)
        rig.wait_idle()
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert any("not supported" in t.lower() for t in rig.dialogs.texts()), rig.dialogs.texts()
        assert rig.gpu.inference_calls == []
        assert rig.final_files() == []


class TestModelLifecycle:
    def test_model_is_loaded_once_for_the_whole_queue_and_freed_afterwards(self, rig):
        rig.submit(rig.write_text("a.txt", sample_text(900, "Alpha")), XTTS, name="a")
        rig.submit(rig.write_text("b.txt", sample_text(900, "Beta")), XTTS, name="b")
        rig.wait_idle()
        assert len(rig.final_files()) == 2
        assert rig.gpu.loads == 1
        rig.wait_for(lambda: rig.gpu.allocated() == 0, what="VRAM to be released")
        assert rig.gpu.empty_cache_calls >= 1
        rig.wait_for(lambda: "not loaded" in label(rig.window.tts_backend_selector._xtts_status_loaded),
                     what="indicator to go back to 'not loaded'")
        assert not rig.window.tts_backend_selector._btn_xtts_unload.isEnabled()

    def test_indicator_shows_loaded_and_unload_is_locked_while_generating(self, rig):
        sel = selected_xtts(rig)
        rig.gpu.gate = gate = threading.Event()
        rig.submit(rig.write_text(text=sample_text(3000)), XTTS)
        rig.wait_for(lambda: rig.gpu.entered.is_set(), what="synthesis to start")
        sel.refresh_xtts_model_state()
        assert "✅" in label(sel._xtts_status_loaded)
        assert not sel._btn_xtts_unload.isEnabled(), "unloading mid-synthesis would corrupt the task"
        assert rig.gpu.allocated() == rig.gpu.MODEL_BYTES
        gate.set()
        rig.wait_idle()
        assert len(rig.final_files()) == 1

    def test_manual_unload_button_works_even_when_free_vram_is_below_the_threshold(self, make_rig):
        rig = make_rig(total_vram_gb=6.0)
        sel = selected_xtts(rig)
        rig.container.xtts_audio_generator._ensure_model_loaded()
        rig.pump(0.1)
        sel._run_xtts_check()
        rig.wait_for(lambda: "✅" in label(sel._xtts_status_loaded), what="loaded indicator")
        assert rig.gpu.allocated() == rig.gpu.MODEL_BYTES
        # 6 GB total - 2.5 GB model => free VRAM is below the 4 GB readiness threshold
        assert sel._btn_xtts_unload.isEnabled()
        sel._btn_xtts_unload.click()
        rig.wait_for(lambda: rig.gpu.allocated() == 0, what="VRAM to be released by the button")
        rig.wait_for(lambda: not sel._btn_xtts_unload.isEnabled(), what="button to lock again")
        assert "not loaded" in label(sel._xtts_status_loaded)

    def test_a_task_after_a_manual_unload_reloads_the_model(self, rig):
        rig.submit(rig.write_text("a.txt", sample_text(600)), XTTS, name="a")
        rig.wait_idle()
        rig.wait_for(lambda: rig.gpu.allocated() == 0, what="VRAM release")
        rig.submit(rig.write_text("b.txt", sample_text(600)), XTTS, name="b")
        rig.wait_idle()
        assert rig.gpu.loads == 2
        assert len(rig.final_files()) == 2

    def test_closing_the_application_frees_the_gpu(self, rig):
        rig.gpu.gate = gate = threading.Event()
        rig.submit(rig.write_text(text=sample_text(3000)), XTTS)
        rig.wait_for(lambda: rig.gpu.entered.is_set(), what="synthesis")
        threading.Timer(3.0, gate.set).start()
        rig.close()
        gate.set()
        assert rig.gpu.allocated() == 0


class TestFailureHandling:
    def test_out_of_memory_mid_task_reloads_and_finishes_without_holes(self, rig):
        rig.fast_retries()
        rig.gpu.oom_on_calls = {2}
        text = sample_text(5000)
        rig.submit(rig.write_text(text=text), XTTS)
        rig.wait_idle(timeout=90)
        (final,) = rig.final_files()
        assert rig.gpu.loads >= 2, "the model must be reloaded after an OOM"
        assert silent_fraction(final) < 0.15, "OOM must not turn the rest of the batch into silence"
        assert mp3_duration(final) == pytest.approx(len(text) * 0.008, rel=0.4)
        assert rig.dialogs.messages == []

    def test_one_transient_failure_is_retried(self, rig):
        rig.fast_retries()
        rig.gpu.fail_calls = {3: RuntimeError("cuDNN error: transient")}
        rig.submit(rig.write_text(text=sample_text(4000)), XTTS)
        rig.wait_idle(timeout=90)
        (final,) = rig.final_files()
        assert silent_fraction(final) < 0.15
        assert rig.dialogs.messages == []

    def test_persistent_engine_failure_fails_the_task_and_never_writes_a_silent_book(self, rig):
        rig.fast_retries()
        rig.gpu.always_fail = RuntimeError("phonemizer crashed")
        rig.submit(rig.write_text(text=sample_text(2400)), XTTS)
        rig.wait_idle(timeout=90)
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert rig.final_files() == []
        assert rig.dialogs.texts()

    def test_model_load_error_fails_clearly_and_retry_recovers(self, rig):
        rig.fast_retries()
        rig.gpu.load_error = RuntimeError("CUDA error: device-side assert")
        rig.submit(rig.write_text(text=sample_text(1800)), XTTS)
        rig.wait_idle(timeout=90)
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert rig.final_files() == []
        assert rig.gpu.allocated() == 0

        rig.gpu.load_error = None
        rig.queue_button(task, "pause").click()
        rig.wait_idle(timeout=90)
        assert len(rig.final_files()) == 1

    def test_failed_xtts_task_releases_the_model(self, rig):
        rig.fast_retries()
        rig.gpu.always_fail = RuntimeError("boom")
        rig.submit(rig.write_text(text=sample_text(1200)), XTTS)
        rig.wait_idle(timeout=90)
        rig.wait_for(lambda: rig.gpu.allocated() == 0, what="VRAM release after failure")


class TestConcurrencyAndCancel:
    def test_synthesis_is_never_parallel_even_if_the_user_picked_many_threads(self, rig):
        rig.window.thread_selector.show()
        rig.set_threads(4)
        rig.select_backend(XTTS)
        rig.submit(rig.write_text(text=sample_text(8000)), XTTS)
        rig.wait_idle(timeout=90)
        assert len(rig.gpu.inference_calls) > 4
        assert rig.gpu.max_concurrent == 1
        assert rig.tasks() == [] and len(rig.final_files()) == 1

    def test_cancel_during_synthesis_returns_quickly_and_frees_the_gpu(self, rig):
        rig.gpu.gate = gate = threading.Event()
        rig.submit(rig.write_text(text=sample_text(6000)), XTTS)
        rig.wait_for(lambda: rig.gpu.entered.is_set(), what="synthesis")
        (task,) = rig.tasks()
        threading.Timer(4.0, gate.set).start()
        started = time.monotonic()
        rig.queue_button(task, "delete").click()
        assert time.monotonic() - started < 2.0
        gate.set()
        rig.gpu.gate = None
        rig.wait_for(lambda: rig.gpu.allocated() == 0, timeout=30, what="VRAM release after cancel")
        assert rig.tasks() == [] and rig.final_files() == []
        rig.wait_for(lambda: rig.leftover_work_dirs() == [], what="temporary files removed")

        rig.submit(rig.write_text("next.txt", "The next task works normally."), XTTS, name="next")
        rig.wait_idle()
        assert len(rig.final_files()) == 1
