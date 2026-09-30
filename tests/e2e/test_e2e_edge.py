"""End-to-end scenarios for Edge TTS (cloud). Only the ``edge_tts`` network module is faked."""

import pytest

from src.domain.models import TaskStatus, TtsBackend
from tests.e2e.rig import dominant_freqs, freq_for, sample_text, silent_fraction

pytestmark = pytest.mark.e2e

EDGE = TtsBackend.EDGE_TTS
VOICES = {
    ("English", "Male"): "en-US-GuyNeural", ("English", "Female"): "en-US-AriaNeural",
    ("Ukrainian", "Male"): "uk-UA-OstapNeural", ("Ukrainian", "Female"): "uk-UA-PolinaNeural",
    ("German", "Male"): "de-DE-ConradNeural", ("German", "Female"): "de-DE-KatjaNeural",
    ("Russian", "Male"): "ru-RU-DmitryNeural", ("Russian", "Female"): "ru-RU-SvetlanaNeural",
    ("French", "Male"): "fr-FR-HenriNeural", ("French", "Female"): "fr-FR-DeniseNeural",
    ("Spanish", "Male"): "es-ES-AlvaroNeural", ("Spanish", "Female"): "es-ES-ElviraNeural",
    ("Italian", "Male"): "it-IT-ValerioNeural", ("Italian", "Female"): "it-IT-ElsaNeural",
}


class TestVoices:
    @pytest.mark.parametrize("language,gender", list(VOICES))
    def test_language_and_gender_select_the_right_cloud_voice(self, rig, language, gender):
        rig.submit(rig.write_text(text=sample_text(600)), EDGE, language=language, gender=gender)
        rig.wait_idle()
        voice = VOICES[(language, gender)]
        assert rig.edge.voices_used() == {voice}
        (final,) = rig.final_files()
        freqs = [f for f in dominant_freqs(final) if f > 50]
        assert sum(freqs) / len(freqs) == pytest.approx(freq_for(voice), abs=25)


class TestIsolation:
    def test_cloud_mode_never_touches_docker_or_the_gpu(self, rig):
        rig.submit(rig.write_text(), EDGE)
        rig.wait_idle()
        rig.pump(0.5)
        assert rig.docker.up_calls == [] and rig.docker.down_calls == 0 and rig.docker.pull_calls == 0
        assert rig.gpu.loads == 0 and rig.gpu.inference_calls == [] and rig.gpu.empty_cache_calls == 0
        assert rig.server.requests == []


class TestNetworkBehaviour:
    def test_transient_errors_are_retried_transparently(self, rig):
        rig.fast_retries()
        rig.edge.failures = [ConnectionResetError("reset"), TimeoutError("slow"), ConnectionResetError("reset")]
        rig.submit(rig.write_text(text=sample_text(2400)), EDGE)
        rig.wait_idle(timeout=90)
        (final,) = rig.final_files()
        assert silent_fraction(final) < 0.15
        assert rig.dialogs.messages == []

    def test_a_network_outage_is_waited_out_without_toggling_the_users_wifi(self, rig):
        rig.fast_retries()
        rig.network.online = False
        rig.network.recover_on_wait = True
        rig.edge.failures = [ConnectionResetError("network is unreachable")]
        rig.submit(rig.write_text(text=sample_text(1200)), EDGE)
        rig.wait_idle(timeout=90)
        assert len(rig.final_files()) == 1
        assert rig.network.wait_calls, "the app should have waited for the network to return"
        assert all(call["auto_toggle_wlan"] is False for call in rig.network.wait_calls), rig.network.wait_calls

    def test_permanent_outage_fails_the_task_and_retry_works_when_back_online(self, rig):
        rig.fast_retries()
        rig.break_engine(EDGE)
        rig.submit(rig.write_text(text=sample_text(1200)), EDGE)
        rig.wait_idle(timeout=90)
        (task,) = rig.tasks()
        assert task.status == TaskStatus.FAILED
        assert rig.final_files() == []
        rig.heal_engine(EDGE)
        rig.queue_button(task, "pause").click()
        rig.wait_idle(timeout=90)
        assert len(rig.final_files()) == 1


class TestThreads:
    def test_thread_selector_controls_parallelism_for_cloud_synthesis(self, rig):
        rig.set_threads(4)
        rig.submit(rig.write_text(text=sample_text(9000)), EDGE)
        rig.wait_idle(timeout=90)
        assert 2 <= rig.edge.max_active <= 4 + 1
        assert len(rig.final_files()) == 1

    def test_single_thread_stays_sequential(self, rig):
        rig.set_threads(1)
        rig.submit(rig.write_text(text=sample_text(6000)), EDGE)
        rig.wait_idle(timeout=90)
        assert rig.edge.max_active == 1
