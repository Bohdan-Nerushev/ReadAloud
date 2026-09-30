"""
Regression tests for H1 (circuit breaker), H2 (generation isolation), H3 (cooperative
cancel / non-blocking stop), M2 (network probing) and M3 (truncated Piper streams).
"""

import asyncio
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("edge_tts", MagicMock())

from src.application.services.generation_service import GenerationService
from src.domain.audio_generator import AudioGenerator
from src.domain.exceptions import PiperConnectionException, PiperNotAvailableException
from src.domain.models import AudioChunk
from src.domain.piper_audio_generator import PiperAudioGenerator
from src.domain.xtts_audio_generator import XttsAudioGenerator
from src.infrastructure.network_manager import NetworkManager
from src.infrastructure.xtts_model_manager import XttsModelManager
from tests.unit._lifecycle_helpers import make_config


def chunk(n, text="Hello there"):
    return AudioChunk(chunk_number=n, text_content=text)


# ---------------------------------------------------------------------------
# M3: truncated Piper stream
# ---------------------------------------------------------------------------

class _FakeEvent:
    def __init__(self, kind, payload=None):
        self.type = kind
        self.payload = payload


class _FakeClient:
    """Async context manager yielding scripted Wyoming events; None = connection closed."""

    def __init__(self, events):
        self._events = list(events)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def write_event(self, event):
        pass

    async def read_event(self):
        return self._events.pop(0) if self._events else None


def _patch_wyoming(events):
    from src.domain import piper_audio_generator as mod

    start = MagicMock(channels=1, width=2, rate=22050)
    audio = MagicMock(audio=b"\x01\x00" * 100)
    mod_patches = [
        patch.object(mod, "AsyncTcpClient", lambda host, port: _FakeClient(events)),
        patch.object(mod.AudioStart, "is_type", staticmethod(lambda t: t == "start")),
        patch.object(mod.AudioStart, "from_event", staticmethod(lambda e: start)),
        patch.object(mod.WyomingAudioChunk, "is_type", staticmethod(lambda t: t == "chunk")),
        patch.object(mod.WyomingAudioChunk, "from_event", staticmethod(lambda e: audio)),
        patch.object(mod.AudioStop, "is_type", staticmethod(lambda t: t == "stop")),
        patch.object(mod, "Synthesize", MagicMock()),
    ]
    return mod_patches


class TestTruncatedPiperStream:
    def _run(self, tmp_path, events):
        gen = PiperAudioGenerator()
        wav = tmp_path / "1.wav.tmp"
        patches = _patch_wyoming(events)
        for p in patches:
            p.start()
        try:
            asyncio.run(gen._synthesise_to_wav("text", "voice", wav))
        finally:
            for p in patches:
                p.stop()
        return wav

    def test_complete_stream_is_accepted(self, tmp_path):
        wav = self._run(tmp_path, [_FakeEvent("start"), _FakeEvent("chunk"), _FakeEvent("stop")])
        assert wav.exists() and wav.stat().st_size > 0

    def test_connection_drop_before_stop_is_a_failure(self, tmp_path):
        with pytest.raises(PiperConnectionException, match="AudioStop"):
            self._run(tmp_path, [_FakeEvent("start"), _FakeEvent("chunk"), _FakeEvent("chunk")])

    def test_truncated_wav_is_deleted(self, tmp_path):
        wav = tmp_path / "1.wav.tmp"
        with pytest.raises(PiperConnectionException):
            self._run(tmp_path, [_FakeEvent("start"), _FakeEvent("chunk")])
        assert not wav.exists()

    def test_immediate_close_is_a_failure(self, tmp_path):
        with pytest.raises(PiperConnectionException):
            self._run(tmp_path, [])

    def test_truncated_chunk_is_retried_and_never_returned_as_success(self, tmp_path):
        gen = PiperAudioGenerator()
        calls = {"n": 0}

        async def flaky(text, voice, wav_path):
            calls["n"] += 1
            wav_path.write_bytes(b"partial")
            if calls["n"] < 3:
                raise PiperConnectionException("closed before AudioStop")
            wav_path.write_bytes(b"full")

        def convert(wav, mp3_tmp):
            mp3_tmp.write_bytes(b"mp3")

        try:
            with patch.object(gen, "_synthesise_to_wav", side_effect=flaky), \
                    patch.object(gen, "_convert_wav_to_mp3", side_effect=convert), \
                    patch.object(gen, "_get_mp3_duration", return_value=1.0):
                results = gen.generate_audio_batch([chunk(1)], "en", "male", str(tmp_path),
                                                   max_retries=5, backoff=0.0)
        finally:
            gen.close()
        assert calls["n"] == 3
        assert results[0] is not None


# ---------------------------------------------------------------------------
# H1: circuit breaker on ConnectionRefusedError
# ---------------------------------------------------------------------------

class TestPiperCircuitBreaker:
    def test_dead_server_aborts_batch_after_few_refusals(self, tmp_path):
        gen = PiperAudioGenerator()
        attempts = {"n": 0}

        async def refuse(text, voice, wav_path):
            attempts["n"] += 1
            raise ConnectionRefusedError()

        try:
            with patch.object(gen, "_synthesise_to_wav", side_effect=refuse):
                with pytest.raises(PiperNotAvailableException, match="refused"):
                    gen.generate_audio_batch([chunk(i) for i in range(1, 11)], "en", "male",
                                             str(tmp_path), max_retries=10, backoff=0.0)
        finally:
            gen.close()
        # one chunk, MAX refusals - not 10 chunks x 10 attempts
        assert attempts["n"] == PiperAudioGenerator.MAX_CONSECUTIVE_REFUSALS

    def test_refusals_then_success_does_not_trip_breaker(self, tmp_path):
        gen = PiperAudioGenerator()
        calls = {"n": 0}

        async def flaky(text, voice, wav_path):
            calls["n"] += 1
            if calls["n"] < PiperAudioGenerator.MAX_CONSECUTIVE_REFUSALS:
                raise ConnectionRefusedError()
            wav_path.write_bytes(b"w")

        try:
            with patch.object(gen, "_synthesise_to_wav", side_effect=flaky), \
                    patch.object(gen, "_convert_wav_to_mp3",
                                 side_effect=lambda w, m: m.write_bytes(b"m")), \
                    patch.object(gen, "_get_mp3_duration", return_value=1.0):
                results = gen.generate_audio_batch([chunk(1)], "en", "male", str(tmp_path),
                                                   max_retries=10, backoff=0.0)
        finally:
            gen.close()
        assert results[0] is not None

    def test_service_turns_dead_server_into_single_abort(self, tmp_path):
        gen = PiperAudioGenerator()
        service = GenerationService(gen)
        aborted, failed = [], []
        service.generationAborted.connect(aborted.append)
        service.batchFailed.connect(lambda *a: failed.append(a))

        async def refuse(text, voice, wav_path):
            raise ConnectionRefusedError()

        try:
            with patch.object(gen, "_synthesise_to_wav", side_effect=refuse):
                service._output_dir = str(tmp_path)
                service._generate_batch_safe([chunk(1), chunk(2)], "en", "male", "cid", 1, 10)
        finally:
            gen.close()
        assert len(aborted) == 1 and "refused" in aborted[0]
        assert not failed


# ---------------------------------------------------------------------------
# H3: cooperative cancellation
# ---------------------------------------------------------------------------

class TestAsyncGeneratorCancel:
    @pytest.mark.parametrize("factory,target", [
        (lambda: PiperAudioGenerator(), "_generate_one_with_retry"),
        (lambda: AudioGenerator(), "_generate_one_with_retry"),
    ])
    def test_cancel_interrupts_running_batch_quickly(self, tmp_path, factory, target):
        gen = factory()
        started = threading.Event()

        async def hang(*a, **k):
            started.set()
            await asyncio.sleep(60)

        result = {}

        def run():
            with patch.object(gen, target, side_effect=hang):
                result["r"] = gen.generate_audio_batch([chunk(1), chunk(2)], "en", "male", str(tmp_path))

        t = threading.Thread(target=run, daemon=True)
        t0 = time.time()
        with patch.object(gen, target, side_effect=hang):
            t.start()
            assert started.wait(5)
            gen.cancel()
            t.join(5)
        gen.close()
        assert not t.is_alive(), "batch kept running after cancel()"
        assert time.time() - t0 < 5
        assert result["r"] == [None, None]

    def test_cancel_is_not_sticky(self, tmp_path):
        gen = PiperAudioGenerator()
        gen.cancel()

        async def ok(*a, **k):
            return str(tmp_path / "1.mp3"), 1.0

        try:
            with patch.object(gen, "_generate_one_with_retry", side_effect=ok):
                results = gen.generate_audio_batch([chunk(1)], "en", "male", str(tmp_path))
        finally:
            gen.close()
        assert results[0] is not None

    def test_cancel_with_nothing_running_is_a_noop(self):
        gen = AudioGenerator()
        gen.cancel()
        gen.close()


class TestXttsCancel:
    def _gen(self):
        manager = MagicMock(spec=XttsModelManager)
        manager.get_reference_audio_path.return_value = Path("/tmp/en/male.wav")
        gen = XttsAudioGenerator(model_manager=manager)
        gen._ensure_model_loaded = MagicMock()
        return gen

    def test_cancel_stops_before_next_chunk(self, tmp_path):
        gen = self._gen()
        done = []

        def synth(chunk, xtts_language, speaker_wav, output_path):
            done.append(chunk.chunk_number)
            gen.cancel()
            return str(tmp_path / f"{chunk.chunk_number}.mp3"), 1.0

        gen._synthesise_chunk = synth
        results = gen.generate_audio_batch([chunk(i) for i in range(1, 6)], "en", "male", str(tmp_path))
        assert done == [1]
        assert results[0] is not None and results[1:] == [None] * 4

    def test_cancel_interrupts_backoff_sleep(self, tmp_path):
        gen = self._gen()
        gen._synthesise_chunk = MagicMock(side_effect=RuntimeError("transient"))
        t0 = time.time()
        timer = threading.Timer(0.3, gen.cancel)
        timer.start()
        results = gen.generate_audio_batch([chunk(1)], "en", "male", str(tmp_path),
                                           max_retries=5, backoff=30.0)
        timer.join()
        assert time.time() - t0 < 5
        assert results == [None]

    def test_cancel_is_not_sticky(self, tmp_path):
        gen = self._gen()
        gen.cancel()
        gen._synthesise_chunk = lambda **k: (str(tmp_path / "1.mp3"), 1.0)
        assert gen.generate_audio_batch([chunk(1)], "en", "male", str(tmp_path))[0] is not None

    def test_last_error_recorded_for_failed_chunk(self, tmp_path):
        gen = self._gen()
        gen._synthesise_chunk = MagicMock(side_effect=RuntimeError("phonemizer crashed"))
        with patch("time.sleep"):
            gen.generate_audio_batch([chunk(1)], "en", "male", str(tmp_path), max_retries=1, backoff=0.0)
        assert "phonemizer crashed" in gen.last_error


# ---------------------------------------------------------------------------
# H2: generation id isolation
# ---------------------------------------------------------------------------

class _Task:
    def __init__(self):
        import uuid
        self.id = uuid.uuid4()
        self.config = MagicMock(language="en", gender="male", effective_thread_count=1)


class TestGenerationIsolation:
    def _service(self):
        gen = MagicMock()
        gen.last_error = None
        return GenerationService(gen), gen

    def test_ids_change_on_start_and_stop(self):
        service, _ = self._service()
        with patch("src.application.services.generation_service.ThreadManager"):
            before = service.generation_id
            service.start_generation(_Task(), [chunk(1)], "/out/a")
            mid = service.generation_id
            service.stop()
        assert before < mid < service.generation_id

    def test_stale_chunk_callback_is_dropped(self):
        service, _ = self._service()
        got = []
        service.chunkGenerated.connect(lambda *a: got.append(a))
        with patch("src.application.services.generation_service.ThreadManager"):
            service.start_generation(_Task(), [chunk(1)], "/out/a")
            old_id = service.generation_id
            service.start_generation(_Task(), [chunk(1)], "/out/b")
        service._on_chunk_generated_callback(1, "/out/a/1.mp3", 1.0, generation_id=old_id)
        assert got == []
        service._on_chunk_generated_callback(1, "/out/b/1.mp3", 1.0, generation_id=service.generation_id)
        assert got == [(1, "/out/b/1.mp3", 1.0)]

    def test_late_batch_from_old_generation_reports_nothing_and_does_not_use_new_dir(self):
        service, gen = self._service()
        used_dirs, events = [], []
        service.batchGenerated.connect(lambda r: events.append("batch"))
        service.chunkFailed.connect(lambda *a: events.append("failed"))
        service.batchFailed.connect(lambda *a: events.append("batch_failed"))
        release = threading.Event()

        def slow_batch(batch, lang, gender, out_dir, **kw):
            used_dirs.append(out_dir)
            release.wait(5)
            kw["chunk_callback"](1, f"{out_dir}/1.mp3", 1.0)
            return [(f"{out_dir}/1.mp3", 1.0)]

        gen.generate_audio_batch.side_effect = slow_batch
        got = []
        service.chunkGenerated.connect(lambda *a: got.append(a))

        with patch("src.application.services.generation_service.ThreadManager"):
            service.start_generation(_Task(), [chunk(1)], "/out/old")
            old_id = service.generation_id
            worker = threading.Thread(
                target=service._generate_batch_safe,
                args=([chunk(1)], "en", "male", "cid", 1, 10, old_id, "/out/old"))
            worker.start()
            time.sleep(0.1)
            service.stop()
            service.start_generation(_Task(), [chunk(1)], "/out/new")
            release.set()
            worker.join(5)

        assert used_dirs == ["/out/old"]
        assert got == [] and events == []

    def test_failure_of_stale_batch_is_not_reported(self):
        service, gen = self._service()
        gen.generate_audio_batch.side_effect = RuntimeError("late boom")
        events = []
        service.batchFailed.connect(lambda *a: events.append(a))
        service.generationAborted.connect(lambda *a: events.append(a))
        with patch("src.application.services.generation_service.ThreadManager"):
            service.start_generation(_Task(), [chunk(1)], "/out/a")
            old_id = service.generation_id
            service.stop()
        service._generate_batch_safe([chunk(1)], "en", "male", "cid", 1, 10, old_id, "/out/a")
        assert events == []

    def test_submitted_batches_carry_generation_id_and_output_dir(self):
        service, _ = self._service()
        with patch("src.application.services.generation_service.ThreadManager") as tm:
            service.start_generation(_Task(), [chunk(1)], "/out/a")
            submitted = tm.return_value.submit_task.call_args[0][0]
        assert submitted.keywords["generation_id"] == service.generation_id
        assert submitted.keywords["output_dir"] == "/out/a"

    def test_stop_cancels_generator(self):
        service, gen = self._service()
        service.stop()
        gen.cancel.assert_called_once()

    def test_generator_without_cancel_is_tolerated(self):
        class Plain:
            last_error = None

        service = GenerationService(Plain())
        service.stop()

    def test_controller_failure_stops_generation_service(self, tmp_path):
        from tests.unit._lifecycle_helpers import make_controller
        controller, queue, _, generation, _ = make_controller(tmp_path / "s.json")
        queue.add_task(make_config(tmp_path))
        queue.get_next_task()
        controller._handle_task_failure("boom")
        generation.stop.assert_called()


# ---------------------------------------------------------------------------
# H3: GUI thread never waits for running batches
# ---------------------------------------------------------------------------

class TestGuiPathsDoNotBlock:
    def _setup(self, tmp_path):
        from tests.unit._lifecycle_helpers import make_controller
        controller, queue, _, generation, assembly = make_controller(tmp_path / "s.json")
        queue.add_task(make_config(tmp_path, "A"))
        task = queue.get_next_task()
        return controller, queue, generation, task

    @staticmethod
    def _waited(generation):
        return [c for c in generation.stop.call_args_list if c.kwargs.get("wait")]

    def test_cancel_current_task(self, tmp_path):
        controller, _, generation, task = self._setup(tmp_path)
        controller.cancel_current_task()
        assert generation.stop.called and not self._waited(generation)

    def test_stop_generation(self, tmp_path):
        controller, _, generation, task = self._setup(tmp_path)
        controller.stop_generation()
        assert generation.stop.called and not self._waited(generation)

    def test_retry_generation_of_current_task(self, tmp_path):
        controller, _, generation, task = self._setup(tmp_path)
        controller.retry_generation(str(task.id))
        assert generation.stop.called and not self._waited(generation)

    def test_shutdown_still_waits(self, tmp_path):
        controller, _, generation, task = self._setup(tmp_path)
        controller.shutdown()
        assert self._waited(generation)

    def test_release_of_idle_backends_runs_in_background_runner(self, tmp_path):
        from src.domain.models import TtsBackend
        from tests.unit._lifecycle_helpers import make_controller
        controller, queue, _, generation, _ = make_controller(tmp_path / "s.json")
        piper = MagicMock()
        controller._piper_setup_service = piper
        controller._piper_used = True
        deferred = []
        controller._release_runner = deferred.append
        controller._release_idle_backends()
        piper.stop_container.assert_not_called()      # GUI thread did not do the slow work
        assert len(deferred) == 1
        deferred[0]()
        piper.stop_container.assert_called_once()

    def test_release_skips_backend_needed_again_before_thread_runs(self, tmp_path):
        from src.domain.models import TtsBackend
        from tests.unit._lifecycle_helpers import make_controller
        controller, *_ = make_controller(tmp_path / "s.json")
        piper = MagicMock()
        controller._piper_setup_service = piper
        controller._piper_used = True
        deferred = []
        controller._release_runner = deferred.append
        controller._release_idle_backends()
        controller._backends_needed.add(TtsBackend.PIPER)   # next task started preparing Piper
        deferred[0]()
        piper.stop_container.assert_not_called()


# ---------------------------------------------------------------------------
# M2: network probing
# ---------------------------------------------------------------------------

class TestNetworkProbing:
    def test_wlan_toggle_is_opt_in(self):
        import inspect
        default = inspect.signature(NetworkManager.wait_for_network).parameters["auto_toggle_wlan"].default
        assert default is False

    def test_wait_for_network_does_not_toggle_wlan_by_default(self):
        nm = NetworkManager()
        nm.is_connected = MagicMock(side_effect=[False, False, True])
        nm.toggle_wlan = MagicMock()
        with patch("time.sleep"), patch("time.time", side_effect=[0, 20, 20, 40, 40, 60]):
            nm.wait_for_network(max_wait_seconds=100, check_interval=0)
        nm.toggle_wlan.assert_not_called()

    def test_explicit_opt_in_still_toggles(self):
        nm = NetworkManager()
        nm.is_connected = MagicMock(side_effect=[False, False, True])
        nm.toggle_wlan = MagicMock()
        with patch("time.sleep"), patch("time.time", side_effect=[0, 0, 20, 20, 40, 40, 60]):
            nm.wait_for_network(max_wait_seconds=100, check_interval=0, auto_toggle_wlan=True)
        nm.toggle_wlan.assert_called_once()

    def test_edge_generator_never_enables_wlan_toggle(self):
        gen = AudioGenerator()
        try:
            assert gen._auto_toggle_wlan is False
        finally:
            gen.close()

    def test_connectivity_probe_does_not_block_event_loop(self):
        nm = MagicMock()
        probe_thread = []

        def slow_probe():
            probe_thread.append(threading.current_thread())
            time.sleep(0.4)
            return True

        nm.is_connected.side_effect = slow_probe
        gen = AudioGenerator(network_manager=nm)
        ticks = []

        async def scenario():
            async def ticker():
                for _ in range(8):
                    ticks.append(time.time())
                    await asyncio.sleep(0.05)

            t = asyncio.create_task(ticker())
            up = await gen._network_is_up()
            await t
            return up

        try:
            assert asyncio.run(scenario()) is True
        finally:
            gen.close()
        assert probe_thread[0] is not threading.main_thread()
        # ticker kept running while the probe slept: the loop was not blocked for 0.4 s
        assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.3
