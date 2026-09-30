"""
Regression tests for M9 (progress double counting), M10 (pause inside a running batch),
M11 (fast assembly path after retries), M12 (throttled manifest), M13 (no private generator
access), M14 (no auto-restart of the Piper container) and the audit's "potential" items
B1-B8 (FP16 claims, VRAM flush, Piper voice on a running container, GUI-thread hand-off of
assembly results, stop() vs parallel assemblies, ffmpeg stdin, disk space, lossless batches).
"""

import asyncio
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("edge_tts", MagicMock())

from PyQt6.QtCore import QCoreApplication

from src.application.app_controller import PreparationWorker
from src.domain.base_generator import BaseAudioGenerator
from src.application.services.assembly_service import AssemblyService
from src.application.services.generation_service import GenerationService
from src.application.services.persistence_service import PersistenceService
from src.domain.audio_assembler import AudioAssembler
from src.domain.audio_generator import AudioGenerator
from src.domain.audio_utils import get_mp3_duration
from src.domain.cancellable_batch import PausableMixin
from src.domain.exceptions import BackendNotReadyException, InsufficientDiskSpaceException
from src.domain.models import AudioChunk
from src.domain.piper_audio_generator import PiperAudioGenerator
from src.domain.text_chunker import TextChunker
from src.domain.text_processor import TextProcessor
from src.domain.xtts_audio_generator import XttsAudioGenerator
from src.infrastructure import disk_space
from src.infrastructure.file_manager import FileManager
from src.infrastructure.progress_tracker import ProgressTracker
from src.infrastructure.xtts_model_manager import XttsModelManager
from tests.unit._lifecycle_helpers import make_config, make_controller
from tests.unit.test_backend_readiness import _piper

ROOT = Path(__file__).resolve().parents[2]


def chunk(n, text="Hello there"):
    return AudioChunk(chunk_number=n, text_content=text)


class _Task:
    def __init__(self):
        import uuid
        self.id = uuid.uuid4()
        self.config = MagicMock(language="en", gender="male", effective_thread_count=1)


def wait_until(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ---------------------------------------------------------------------------
# M9: a batch that blows up after partial success
# ---------------------------------------------------------------------------

class TestBatchFailureAfterPartialSuccess:
    def _service(self, total):
        gen = MagicMock()
        gen.last_error = None
        service = GenerationService(gen)
        with patch("src.application.services.generation_service.ThreadManager"):
            service.start_generation(_Task(), [chunk(i) for i in range(1, total + 1)], "/out")
        return service, gen

    def test_delivered_chunks_are_not_counted_twice(self):
        service, gen = self._service(4)

        def boom(batch, lang, gender, out_dir, **kw):
            kw["chunk_callback"](1, "/out/1.mp3", 1.0)
            kw["chunk_callback"](2, "/out/2.mp3", 1.0)
            raise RuntimeError("loop died")

        gen.generate_audio_batch.side_effect = boom
        service._generate_batch_safe([chunk(i) for i in range(1, 5)], "en", "male", "cid", 1)

        processed, total, _, _ = service.get_progress_info()
        assert (processed, total) == (4, 4)
        assert service._progress_tracker.get_completed_count() == 2
        assert service._progress_tracker.get_failed_count() == 2
        assert service.get_progress_percentage() == 100.0

    def test_batch_failed_lists_only_unfinished_chunks(self):
        service, gen = self._service(4)
        failed = []
        service.batchFailed.connect(lambda chunks, msg: failed.append([c.chunk_number for c in chunks]))

        def boom(batch, lang, gender, out_dir, **kw):
            kw["chunk_callback"](1, "/out/1.mp3", 1.0)
            raise RuntimeError("x")

        gen.generate_audio_batch.side_effect = boom
        service._generate_batch_safe([chunk(i) for i in range(1, 5)], "en", "male", "cid", 1)
        assert failed == [[2, 3, 4]]

    def test_batch_failing_before_any_chunk_fails_all(self):
        service, gen = self._service(3)
        gen.generate_audio_batch.side_effect = RuntimeError("x")
        service._generate_batch_safe([chunk(i) for i in range(1, 4)], "en", "male", "cid", 1)
        assert service.get_progress_info()[:2] == (3, 3)
        assert service._progress_tracker.get_failed_count() == 3

    def test_no_failure_signal_when_everything_was_delivered(self):
        service, gen = self._service(2)
        failed = []
        service.batchFailed.connect(lambda *a: failed.append(a))

        def boom(batch, lang, gender, out_dir, **kw):
            kw["chunk_callback"](1, "/out/1.mp3", 1.0)
            kw["chunk_callback"](2, "/out/2.mp3", 1.0)
            raise RuntimeError("late failure")

        gen.generate_audio_batch.side_effect = boom
        service._generate_batch_safe([chunk(1), chunk(2)], "en", "male", "cid", 1)
        assert failed == []
        assert service._progress_tracker.get_failed_count() == 0

    def test_processed_never_exceeds_total_even_with_repeated_reports(self):
        tracker = ProgressTracker(3)
        for _ in range(3):
            tracker.update_progress(True, 1)
            tracker.update_progress(False, 1)
        tracker.update_progress(False, 2)
        tracker.update_progress(False, 2)
        assert tracker.get_processed_count() == 2
        assert tracker.get_progress_percentage() <= 100.0


# ---------------------------------------------------------------------------
# M10: pause takes effect inside a running batch
# ---------------------------------------------------------------------------

class TestPausableMixin:
    def test_flag_lifecycle(self):
        class G(PausableMixin):
            pass

        g = G()
        g._init_pause()
        assert not g.is_paused()
        g.pause()
        assert g.is_paused()
        g.resume()
        assert not g.is_paused()

    def test_sync_gate_returns_immediately_when_not_paused(self):
        class G(PausableMixin):
            pass

        g = G()
        g._init_pause()
        t0 = time.time()
        g._wait_while_paused()
        assert time.time() - t0 < 0.05

    def test_sync_gate_honours_abort_predicate(self):
        class G(PausableMixin):
            pass

        g = G()
        g._init_pause()
        g.pause()
        t0 = time.time()
        g._wait_while_paused(lambda: True)
        assert time.time() - t0 < 0.5


class TestAsyncGeneratorsPauseInsideBatch:
    @pytest.mark.parametrize("factory", [lambda: AudioGenerator(), lambda: PiperAudioGenerator()],
                             ids=["edge", "piper"])
    def test_pause_holds_back_remaining_chunks_then_resume_finishes(self, tmp_path, factory):
        gen = factory()
        started = []

        async def one(*a, **k):
            chunk_obj = k.get("chunk") or a[0]
            started.append(chunk_obj.chunk_number)
            if chunk_obj.chunk_number == 1:
                gen.pause()
            return str(tmp_path / f"{chunk_obj.chunk_number}.mp3"), 1.0

        result = {}

        def run():
            with patch.object(gen, "_generate_one_with_retry", side_effect=one):
                result["r"] = gen.generate_audio_batch(
                    [chunk(i) for i in range(1, 5)], "en", "male", str(tmp_path), max_workers=1
                )

        t = threading.Thread(target=run, daemon=True)
        try:
            with patch.object(gen, "_generate_one_with_retry", side_effect=one):
                t.start()
                time.sleep(0.6)
                assert started == [1], "batch kept synthesising although it was paused"
                assert t.is_alive()
                gen.resume()
                t.join(5)
            assert not t.is_alive()
            assert started == [1, 2, 3, 4]
            assert all(r is not None for r in result["r"])
        finally:
            gen.close()

    @pytest.mark.parametrize("factory", [lambda: AudioGenerator(), lambda: PiperAudioGenerator()],
                             ids=["edge", "piper"])
    def test_cancel_releases_a_paused_batch(self, tmp_path, factory):
        gen = factory()
        gen.pause()
        result = {}

        async def one(*a, **k):
            raise AssertionError("must not start while paused")

        def run():
            with patch.object(gen, "_generate_one_with_retry", side_effect=one):
                result["r"] = gen.generate_audio_batch(
                    [chunk(1), chunk(2)], "en", "male", str(tmp_path), max_workers=1
                )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.3)
        assert t.is_alive()
        gen.cancel()
        t.join(5)
        gen.close()
        assert not t.is_alive()
        assert result["r"] == [None, None]
        assert not gen.is_paused(), "cancel() must clear the pause flag for the next task"

    def test_retry_attempts_also_wait_while_paused(self, tmp_path):
        from src.domain.exceptions import PiperConnectionException
        gen = PiperAudioGenerator()
        attempts = []

        async def failing_synth(text, voice, wav_path):
            attempts.append(1)
            if len(attempts) == 1:
                gen.pause()
            raise PiperConnectionException("flaky")

        result = {}
        t = threading.Thread(
            target=lambda: result.update(
                r=gen.generate_audio_batch([chunk(1)], "en", "male", str(tmp_path),
                                           max_retries=2, backoff=0.0)
            ),
            daemon=True,
        )
        try:
            with patch.object(gen, "_synthesise_to_wav", side_effect=failing_synth):
                t.start()
                time.sleep(0.8)
                assert len(attempts) == 1 and t.is_alive(), "retry ran although paused"
                gen.resume()
                t.join(10)
            assert len(attempts) == 2 and result["r"] == [None]
        finally:
            gen.close()


class TestXttsPause:
    def _gen(self):
        manager = MagicMock(spec=XttsModelManager)
        manager.get_reference_audio_path.return_value = Path("/tmp/en/male.wav")
        gen = XttsAudioGenerator(model_manager=manager)
        gen._ensure_model_loaded = MagicMock()
        return gen

    def test_pause_between_chunks_of_a_running_batch(self, tmp_path):
        gen = self._gen()
        started = []

        def synth(chunk, xtts_language, speaker_wav, output_path):
            started.append(chunk.chunk_number)
            if chunk.chunk_number == 1:
                gen.pause()
            return str(tmp_path / f"{chunk.chunk_number}.mp3"), 1.0

        gen._synthesise_chunk = synth
        result = {}
        t = threading.Thread(
            target=lambda: result.update(
                r=gen.generate_audio_batch([chunk(i) for i in range(1, 5)], "en", "male", str(tmp_path))
            ),
            daemon=True,
        )
        t.start()
        time.sleep(0.5)
        assert started == [1] and t.is_alive()
        gen.resume()
        t.join(5)
        assert started == [1, 2, 3, 4]
        assert all(r is not None for r in result["r"])

    def test_retry_attempt_waits_while_paused(self, tmp_path):
        gen = self._gen()
        calls = []

        def synth(chunk, xtts_language, speaker_wav, output_path):
            calls.append(time.time())
            if len(calls) == 1:
                gen.pause()
                raise RuntimeError("transient")
            return str(tmp_path / "1.mp3"), 1.0

        gen._synthesise_chunk = synth
        t = threading.Thread(
            target=lambda: gen.generate_audio_batch([chunk(1)], "en", "male", str(tmp_path), backoff=0.0),
            daemon=True,
        )
        t.start()
        time.sleep(0.6)
        assert len(calls) == 1 and t.is_alive()
        gen.resume()
        t.join(5)
        assert len(calls) == 2

    def test_cancel_releases_paused_batch(self, tmp_path):
        gen = self._gen()
        gen._synthesise_chunk = MagicMock(side_effect=AssertionError("must not run"))
        gen.pause()
        result = {}
        t = threading.Thread(
            target=lambda: result.update(
                r=gen.generate_audio_batch([chunk(1), chunk(2)], "en", "male", str(tmp_path))
            ),
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        assert t.is_alive()
        gen.cancel()
        t.join(5)
        assert not t.is_alive()
        assert result["r"] == [None, None]
        assert not gen.is_paused()


class TestServicePropagatesPause:
    def _service(self):
        gen = MagicMock()
        gen.last_error = None
        service = GenerationService(gen)
        with patch("src.application.services.generation_service.ThreadManager") as tm:
            tm.return_value.is_paused.return_value = False
            service.start_generation(_Task(), [chunk(1)], "/out")
        return service, gen, tm.return_value

    def test_pause_and_resume_reach_the_generator(self):
        service, gen, tm = self._service()
        gen.pause.reset_mock()
        assert service.pause() is True
        gen.pause.assert_called_once()
        tm.is_paused.return_value = True
        assert service.pause() is False
        gen.resume.assert_called()

    def test_new_generation_starts_unpaused(self):
        service, gen, _ = self._service()
        gen.resume.assert_called()

    def test_generator_whose_pause_fails_is_tolerated(self):
        class Minimal(BaseAudioGenerator):
            def generate_audio_batch(self, *a, **k):
                return []

            def pause(self):
                raise RuntimeError("cannot pause")

        service = GenerationService(Minimal())
        with patch("src.application.services.generation_service.ThreadManager") as tm:
            tm.return_value.is_paused.return_value = False
            service.start_generation(_Task(), [chunk(1)], "/out")
            assert service.pause() is True


# ---------------------------------------------------------------------------
# M11: counting with retries
# ---------------------------------------------------------------------------

def _assembly(batch=3, total=6):
    service = AssemblyService(MagicMock())
    service.BATCH_SIZE = batch
    service.reset(total)
    return service


class TestAssemblyCountingAfterRetries:
    def test_failed_then_retry_success_completes_batch_exactly_once(self):
        s = _assembly()
        assert s.mark_chunk_failed(0) is None
        assert s.mark_chunk_ready(1, 1.0) is None
        assert s.mark_chunk_ready(2, 1.0) == 0          # all resolved (one hole)
        assert s.mark_chunk_ready(0, 2.0) == 0          # retry success must still trigger
        assert s._batch_durations[0] == [2.0, 1.0, 1.0]

    def test_duplicate_ready_reports_do_not_complete_a_batch(self):
        s = _assembly()
        for _ in range(5):
            assert s.mark_chunk_ready(0, 1.0) is None

    def test_duplicate_failures_do_not_complete_a_batch(self):
        s = _assembly()
        for _ in range(5):
            assert s.mark_chunk_failed(0) is None

    def test_late_failure_does_not_discard_a_ready_chunk(self):
        s = _assembly()
        s.mark_chunk_ready(0, 3.0)
        s.mark_chunk_failed(0)
        assert s._batch_durations[0][0] == 3.0
        s.mark_chunk_ready(1, 1.0)
        assert s.mark_chunk_ready(2, 1.0) == 0

    def test_batches_are_independent(self):
        s = _assembly()
        s.mark_chunk_failed(0)
        s.mark_chunk_ready(3, 1.0)
        assert s.mark_chunk_ready(4, 1.0) is None
        assert s.mark_chunk_ready(5, 1.0) == 1

    def test_reset_clears_progress(self):
        s = _assembly()
        s.mark_chunk_ready(0, 1.0)
        s.reset(6)
        s.mark_chunk_ready(1, 1.0)
        assert s.mark_chunk_ready(2, 1.0) is None

    def test_deferred_batch_is_submitted_after_retry_success(self, tmp_path):
        s = _assembly(batch=2, total=2)
        (tmp_path / "2.mp3").write_bytes(b"x")
        files = [str(tmp_path / "1.mp3"), str(tmp_path / "2.mp3")]

        def fake_assemble(paths, out, **kw):
            Path(out).write_bytes(b"part")

        s._audio_assembler.assemble_audio.side_effect = fake_assemble

        s.mark_chunk_failed(0)
        idx = s.mark_chunk_ready(1, 1.0)
        assert idx == 0
        s.submit_batch_by_index(0, files, str(tmp_path), 1.0, "cid")   # deferred: chunk 1 missing
        s._audio_assembler.assemble_audio.assert_not_called()

        (tmp_path / "1.mp3").write_bytes(b"x")                          # the retry succeeded
        idx = s.mark_chunk_ready(0, 1.0)
        assert idx == 0, "retry success must put the batch back on the fast path"
        s.submit_batch_by_index(0, files, str(tmp_path), 1.0, "cid")
        wait_until(lambda: (tmp_path / "part_0.mp3").exists())
        assert (tmp_path / "part_0.mp3").exists()


# ---------------------------------------------------------------------------
# B8: batch assembly does not re-encode MP3s without need
# ---------------------------------------------------------------------------

class TestLosslessConcatenation:
    def _svc(self):
        return AssemblyService(MagicMock())

    def test_speed_one_copies_frames(self):
        s = self._svc()
        s._concat(["a", "b"], "out", 1.0)
        assert s._audio_assembler.assemble_audio.call_args.kwargs["copy_codec"] is True

    def test_speed_change_reencodes(self):
        s = self._svc()
        s._concat(["a", "b"], "out", 1.25)
        kw = s._audio_assembler.assemble_audio.call_args.kwargs
        assert kw["copy_codec"] is False and kw["speed"] == 1.25

    def test_copy_failure_falls_back_to_reencode(self):
        s = self._svc()
        s._audio_assembler.assemble_audio.side_effect = [Exception("ffmpeg exited with code 1"), None]
        s._concat(["a"], "out", 1.0)
        modes = [c.kwargs["copy_codec"] for c in s._audio_assembler.assemble_audio.call_args_list]
        assert modes == [True, False]

    @pytest.mark.parametrize("msg", ["ffmpeg process was cancelled", "terminated (code -15)", "stopped"])
    def test_cancellation_is_not_retried(self, msg):
        s = self._svc()
        s._audio_assembler.assemble_audio.side_effect = Exception(msg)
        with pytest.raises(Exception):
            s._concat(["a"], "out", 1.0)
        assert s._audio_assembler.assemble_audio.call_count == 1

    def test_batch_part_uses_copy_at_normal_speed(self, tmp_path):
        s = _assembly(batch=2, total=2)
        files = []
        for i in (1, 2):
            p = tmp_path / f"{i}.mp3"
            p.write_bytes(b"x")
            files.append(str(p))
            s.mark_chunk_ready(i - 1, 1.0)
        s._audio_assembler.assemble_audio.side_effect = lambda paths, out, **kw: Path(out).write_bytes(b"p")
        s.submit_batch_by_index(0, files, str(tmp_path), 1.0, "cid")
        wait_until(lambda: s._audio_assembler.assemble_audio.called)
        assert s._audio_assembler.assemble_audio.call_args.kwargs["copy_codec"] is True

    def test_full_assembly_fallback_is_also_lossless_at_normal_speed(self):
        s = self._svc()
        s._assemble_full(["a", "b"], Path("out.mp3"), 1.0, "cid")
        assert s._audio_assembler.assemble_audio.call_args.kwargs["copy_codec"] is True


# ---------------------------------------------------------------------------
# M12: manifest
# ---------------------------------------------------------------------------

class TestManifestThrottling:
    def _controller(self, tmp_path):
        controller, queue, persistence, *_ = make_controller(tmp_path / "state.json")
        task = MagicMock()
        task.id = "tid"
        controller._get_current_task = MagicMock(return_value=task)
        controller._audio_dir = str(tmp_path / "audio")
        controller._chunks = [chunk(i) for i in range(1, 6)]
        controller._audio_files = [None] * 5
        controller._persistence_service = MagicMock()
        controller._persistence_service.save_manifest.return_value = True
        return controller

    def test_many_chunk_updates_write_once_within_interval(self, tmp_path):
        c = self._controller(tmp_path)
        for _ in range(200):
            c._update_task_manifest()
        assert c._persistence_service.save_manifest.call_count == 1

    def test_write_happens_again_after_interval(self, tmp_path):
        c = self._controller(tmp_path)
        clock = [1000.0]
        with patch("src.application.app_controller.time.monotonic", lambda: clock[0]):
            c._update_task_manifest()
            clock[0] += c.MANIFEST_MIN_INTERVAL_SECONDS + 0.1
            c._update_task_manifest()
        assert c._persistence_service.save_manifest.call_count == 2

    def test_force_bypasses_throttle(self, tmp_path):
        c = self._controller(tmp_path)
        c._update_task_manifest()
        c._update_task_manifest(force=True)
        assert c._persistence_service.save_manifest.call_count == 2

    def test_failed_write_is_retried_next_time(self, tmp_path):
        c = self._controller(tmp_path)
        c._persistence_service.save_manifest.return_value = False
        c._update_task_manifest()
        c._update_task_manifest()
        assert c._persistence_service.save_manifest.call_count == 2

    def test_payload_lists_completed_chunk_numbers(self, tmp_path):
        c = self._controller(tmp_path)
        c._audio_files = ["a", None, "c", None, None]
        c._update_task_manifest()
        payload = c._persistence_service.save_manifest.call_args[0][1]
        assert payload["completed_chunks"] == [1, 3]
        assert payload["total_chunks"] == 5
        assert "audio_files" not in payload

    def test_chunk_callbacks_do_not_rewrite_per_chunk(self, tmp_path):
        c = self._controller(tmp_path)
        c._is_stopped = False
        c._assembly_service = MagicMock()
        c._assembly_service.BATCH_SIZE = 40
        c._assembly_service.mark_chunk_ready.return_value = None
        for n in range(1, 6):
            c._on_chunk_generated(n, f"/a/{n}.mp3", 1.0)
        assert c._persistence_service.save_manifest.call_count == 1

    def test_batch_completion_forces_a_checkpoint(self, tmp_path):
        c = self._controller(tmp_path)
        c._is_stopped = False
        c._assembly_service = MagicMock()
        c._assembly_service.BATCH_SIZE = 40
        c._assembly_service.mark_chunk_ready.side_effect = [None, 0]
        c._on_chunk_generated(1, "/a/1.mp3", 1.0)
        c._on_chunk_generated(2, "/a/2.mp3", 1.0)
        assert c._persistence_service.save_manifest.call_count == 2

    def test_task_failure_flushes_manifest_before_finalising(self, tmp_path):
        c = self._controller(tmp_path)
        order = []
        c._update_task_manifest = MagicMock(side_effect=lambda force=False: order.append(("manifest", force)))
        c._finalize_task = MagicMock(side_effect=lambda: order.append(("finalize", None)))
        c._generation_service = MagicMock()
        c._handle_task_failure("boom")
        assert order[0] == ("manifest", True)
        assert order[-1][0] == "finalize"

    def test_manifest_is_compact_and_roundtrips(self, tmp_path):
        p = PersistenceService(str(tmp_path / "s.json"))
        data = {"task_id": "t", "completed_chunks": list(range(1, 50)), "note": "Привіт"}
        assert p.save_manifest(str(tmp_path), data)
        raw = (tmp_path / "manifest.json").read_text(encoding="utf-8")
        assert "\n" not in raw and "Привіт" in raw
        import json
        assert json.loads(raw) == data


# ---------------------------------------------------------------------------
# M13: no access to generator internals
# ---------------------------------------------------------------------------

class TestDurationReading:
    def test_controller_does_not_touch_private_generator_members(self):
        src = (ROOT / "src/application/app_controller.py").read_text(encoding="utf-8")
        assert "_audio_generator._get_file_duration_fast" not in src
        assert "_generation_service._audio_generator" not in src

    def test_restore_reads_durations_without_any_generator(self, tmp_path):
        controller, *_ = make_controller(tmp_path / "state.json")
        controller._generation_service = MagicMock()
        controller._generation_service._audio_generator = None   # e.g. previous task's generator gone
        controller._duration_reader = MagicMock(return_value=2.5)
        controller._assembly_service = MagicMock()
        controller._assembly_service.BATCH_SIZE = 40
        controller._assembly_service.mark_chunk_ready.return_value = None

        text_dir, audio_dir = tmp_path / "t", tmp_path / "a"
        text_dir.mkdir()
        audio_dir.mkdir()
        (text_dir / "1.txt").write_text("one", encoding="utf-8")
        (audio_dir / "1.mp3").write_bytes(b"data")
        task = MagicMock()
        task.text_dir, task.audio_dir = str(text_dir), str(audio_dir)
        task.config.speed = 1.0

        assert controller._restore_progress_from_disk(task) is True
        controller._duration_reader.assert_called_once()
        assert controller._audio_files[0] is not None

    def test_corrupt_chunk_is_removed_on_restore(self, tmp_path):
        controller, *_ = make_controller(tmp_path / "state.json")
        controller._duration_reader = MagicMock(return_value=0.0)
        controller._assembly_service = MagicMock()
        text_dir, audio_dir = tmp_path / "t", tmp_path / "a"
        text_dir.mkdir()
        audio_dir.mkdir()
        (text_dir / "1.txt").write_text("one", encoding="utf-8")
        (audio_dir / "1.mp3").write_bytes(b"junk")
        task = MagicMock()
        task.text_dir, task.audio_dir = str(text_dir), str(audio_dir)
        controller._restore_progress_from_disk(task)
        assert not (audio_dir / "1.mp3").exists()

    def test_get_mp3_duration_is_zero_for_missing_or_corrupt_files(self, tmp_path):
        assert get_mp3_duration(str(tmp_path / "nope.mp3")) == 0.0
        bad = tmp_path / "bad.mp3"
        bad.write_bytes(b"not an mp3")
        assert get_mp3_duration(str(bad)) == 0.0

    def test_get_mp3_duration_reads_length(self):
        fake = MagicMock()
        fake.return_value.info.length = 12.5
        with patch("mutagen.mp3.MP3", fake):
            assert get_mp3_duration("x.mp3") == 12.5


# ---------------------------------------------------------------------------
# M14: Piper container is not resurrected
# ---------------------------------------------------------------------------

class TestPiperComposePolicy:
    def test_container_never_auto_restarts(self):
        text = (ROOT / "docker/piper/docker-compose.yml").read_text(encoding="utf-8")
        assert 'restart: "no"' in text
        assert "unless-stopped" not in text and "restart: always" not in text


# ---------------------------------------------------------------------------
# B1: no false FP16 claims; B2: VRAM flush
# ---------------------------------------------------------------------------

class TestXttsPrecisionAndVram:
    def test_code_does_not_claim_fp16_without_half_precision(self):
        sources = [p for p in (ROOT / "src").rglob("*.py")] + [ROOT / "README.md"]
        text = "\n".join(p.read_text(encoding="utf-8") for p in sources)
        converts = ".half(" in text or "autocast" in text or "torch.float16" in text
        assert converts or "FP16" not in text

    def _fake_torch(self, allocated):
        torch = MagicMock()
        torch.cuda.is_available.return_value = True
        torch.cuda.memory_allocated.side_effect = allocated
        return torch

    def test_flush_synchronises_and_releases_cache(self):
        torch = self._fake_torch([2 * 1024 ** 3, 0])
        with patch.dict(sys.modules, {"torch": torch}):
            XttsModelManager.flush_gpu_cache()
        torch.cuda.synchronize.assert_called_once()
        torch.cuda.empty_cache.assert_called_once()
        torch.cuda.ipc_collect.assert_called_once()

    def test_flush_warns_when_tensors_are_still_alive(self, caplog):
        torch = self._fake_torch([3 * 1024 ** 3, 3 * 1024 ** 3])
        with patch.dict(sys.modules, {"torch": torch}), caplog.at_level("WARNING"):
            XttsModelManager.flush_gpu_cache()
        assert "still held" in caplog.text

    def test_flush_without_cuda_is_a_noop(self):
        torch = MagicMock()
        torch.cuda.is_available.return_value = False
        with patch.dict(sys.modules, {"torch": torch}):
            XttsModelManager.flush_gpu_cache()
        torch.cuda.empty_cache.assert_not_called()

    def test_flush_survives_cuda_errors(self):
        torch = MagicMock()
        torch.cuda.is_available.return_value = True
        torch.cuda.memory_allocated.side_effect = RuntimeError("driver gone")
        with patch.dict(sys.modules, {"torch": torch}):
            XttsModelManager.flush_gpu_cache()

    def test_unload_collects_garbage_before_flushing(self):
        order = []
        manager = MagicMock(spec=XttsModelManager)
        manager.flush_gpu_cache.side_effect = lambda: order.append("flush")
        gen = XttsAudioGenerator(model_manager=manager)
        gen._model, gen._model_loaded = object(), True
        with patch("src.domain.xtts_audio_generator.gc.collect", side_effect=lambda: order.append("gc")):
            gen.unload_model()
        assert order == ["gc", "flush"]
        assert gen._model is None and not gen.is_model_loaded


# ---------------------------------------------------------------------------
# B3: Piper voice on an already running container
# ---------------------------------------------------------------------------

class TestPiperVoiceOnRunningContainer:
    def test_missing_voice_is_downloaded_even_if_container_runs(self):
        service, docker, models = _piper(port_open=True, model_present=False)
        service.ensure_ready("en", "female")
        models.download_model.assert_called_once()
        docker.start_piper_compose.assert_not_called()

    def test_present_voice_is_not_downloaded_again(self):
        service, _, models = _piper(port_open=True, model_present=True)
        service.ensure_ready("en", "male")
        models.download_model.assert_not_called()

    def test_download_failure_fails_fast_with_reason(self):
        service, _, models = _piper(port_open=True, model_present=False)
        models.download_model.side_effect = OSError("no network")
        with pytest.raises(BackendNotReadyException, match="no network"):
            service.ensure_ready("en", "male")


# ---------------------------------------------------------------------------
# B4: assembly result is handled on the GUI thread
# ---------------------------------------------------------------------------

class TestAssemblyHandOffToGuiThread:
    def _controller(self, tmp_path):
        controller, *_ = make_controller(tmp_path / "state.json")
        task = MagicMock()
        task.id = "tid"
        controller._get_current_task = MagicMock(return_value=task)
        controller._is_stopped = False
        controller._cleanup_temp_files = MagicMock()
        controller._mark_task_completed = MagicMock()
        controller._handle_assembly_error = MagicMock()
        return controller

    def test_worker_thread_does_not_complete_the_task_itself(self, tmp_path):
        c = self._controller(tmp_path)
        c._assembly_service = MagicMock()
        posted = []
        c._post_to_gui = posted.append
        c._execute_assembly_workflow(tmp_path / "o.mp3", 1.0, "tid")
        c._mark_task_completed.assert_not_called()
        c._cleanup_temp_files.assert_not_called()
        assert len(posted) == 1
        posted[0]()
        c._mark_task_completed.assert_called_once()

    def test_completion_really_runs_on_the_gui_thread(self, tmp_path):
        c = self._controller(tmp_path)
        c._assembly_service = MagicMock()
        seen = {}
        c._mark_task_completed = MagicMock(side_effect=lambda: seen.setdefault("t", threading.current_thread()))
        c._bridge.moveToThread(QCoreApplication.instance().thread())
        worker = threading.Thread(target=c._execute_assembly_workflow, args=(tmp_path / "o.mp3", 1.0, "tid"))
        worker.start()
        worker.join()
        assert wait_until(lambda: (QCoreApplication.processEvents() or "t" in seen))
        assert seen["t"] is threading.main_thread()

    def test_error_is_reported_on_gui_thread_path(self, tmp_path):
        c = self._controller(tmp_path)
        c._assembly_service = MagicMock()
        c._assembly_service.assemble_final.side_effect = RuntimeError("ffmpeg died")
        posted = []
        c._post_to_gui = posted.append
        c._execute_assembly_workflow(tmp_path / "o.mp3", 1.0, "tid")
        c._handle_assembly_error.assert_not_called()
        posted[0]()
        c._handle_assembly_error.assert_called_once()
        c._mark_task_completed.assert_not_called()

    def test_result_for_another_task_is_ignored(self, tmp_path):
        c = self._controller(tmp_path)
        c._on_assembly_finished("older-task", None)
        c._mark_task_completed.assert_not_called()
        c._cleanup_temp_files.assert_not_called()

    def test_result_after_stop_is_ignored(self, tmp_path):
        c = self._controller(tmp_path)
        c._is_stopped = True
        c._on_assembly_finished("tid", None)
        c._on_assembly_finished("tid", RuntimeError("x"))
        c._mark_task_completed.assert_not_called()
        c._handle_assembly_error.assert_not_called()

    def test_result_without_current_task_is_ignored(self, tmp_path):
        c = self._controller(tmp_path)
        c._get_current_task = MagicMock(return_value=None)
        c._on_assembly_finished("tid", None)
        c._mark_task_completed.assert_not_called()

    def test_success_cleans_up_then_completes(self, tmp_path):
        c = self._controller(tmp_path)
        order = []
        c._cleanup_temp_files.side_effect = lambda: order.append("cleanup")
        c._mark_task_completed.side_effect = lambda: order.append("done")
        c._on_assembly_finished("tid", None)
        assert order == ["cleanup", "done"]


# ---------------------------------------------------------------------------
# B5/B6: AudioAssembler stop() and ffmpeg stdin
# ---------------------------------------------------------------------------

class TestAssemblerStopAndStdin:
    def test_stop_is_not_swallowed_by_another_assembly_starting(self, tmp_path):
        assembler = AudioAssembler()
        observed = {}
        a_inside, release_a = threading.Event(), threading.Event()

        def fake_execute(cmd, total, speed, callback):
            name = threading.current_thread().name
            if name == "A":
                a_inside.set()
                release_a.wait(5)
            observed[name] = assembler._stopped

        with patch.object(assembler, "_execute_ffmpeg", side_effect=fake_execute), \
                patch.object(assembler, "_calculate_total_duration", return_value=1.0), \
                patch.object(assembler, "_create_concat_list"), \
                patch("src.domain.audio_assembler.os.replace"):
            a = threading.Thread(target=lambda: assembler.assemble_audio(["x"], str(tmp_path / "a.mp3")), name="A")
            a.start()
            assert a_inside.wait(5)
            assembler.stop()
            b = threading.Thread(target=lambda: assembler.assemble_audio(["x"], str(tmp_path / "b.mp3")), name="B")
            b.start()
            b.join(5)
            release_a.set()
            a.join(5)
        assert observed["A"] is True, "stop() was lost when a second assembly started"
        assert observed["B"] is False

    def test_assembly_started_after_stop_runs_normally(self, tmp_path):
        assembler = AudioAssembler()
        assembler.stop()
        seen = {}
        with patch.object(assembler, "_execute_ffmpeg", side_effect=lambda *a: seen.setdefault("s", assembler._stopped)), \
                patch.object(assembler, "_calculate_total_duration", return_value=1.0), \
                patch.object(assembler, "_create_concat_list"), \
                patch("src.domain.audio_assembler.os.replace"):
            assembler.assemble_audio(["x"], str(tmp_path / "a.mp3"))
        assert seen["s"] is False

    def test_ffmpeg_never_launches_after_stop(self):
        assembler = AudioAssembler()
        assembler._local.epoch = assembler._stop_epoch
        assembler.stop()
        with patch("src.domain.audio_assembler.subprocess.Popen") as popen:
            with pytest.raises(Exception, match="cancelled"):
                assembler._execute_ffmpeg(["ffmpeg"], 1.0, 1.0, None)
        popen.assert_not_called()

    def test_command_disables_stdin(self):
        cmd = AudioAssembler()._build_ffmpeg_command(Path("l.txt"), Path("o.mp3"), 1.0, True)
        assert "-nostdin" in cmd

    def test_popen_gets_devnull_stdin(self):
        assembler = AudioAssembler()
        process = MagicMock()
        process.returncode = 0
        with patch("src.domain.audio_assembler.subprocess.Popen", return_value=process) as popen, \
                patch.object(assembler, "_monitor_process", return_value=""):
            assembler._execute_ffmpeg(["ffmpeg"], 1.0, 1.0, None)
        assert popen.call_args.kwargs["stdin"] == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# B7: free disk space
# ---------------------------------------------------------------------------

class _Usage:
    def __init__(self, free):
        self.free = free


class TestDiskSpace:
    def test_enough_space_passes(self, tmp_path):
        with patch.object(disk_space.shutil, "disk_usage", return_value=_Usage(10 * 1024 ** 4)):
            disk_space.ensure_free_space(1_000_000, str(tmp_path), str(tmp_path))

    def test_too_little_space_raises_with_numbers(self, tmp_path):
        with patch.object(disk_space.shutil, "disk_usage", return_value=_Usage(1024)):
            with pytest.raises(InsufficientDiskSpaceException, match="MB"):
                disk_space.ensure_free_space(1_000_000, str(tmp_path), str(tmp_path))

    def test_same_device_needs_add_up(self, tmp_path):
        chars = 1_000
        one = disk_space.SAFETY_MARGIN_BYTES + disk_space.estimate_bytes(chars, disk_space.WORKSPACE_COPIES)
        with patch.object(disk_space.shutil, "disk_usage", return_value=_Usage(one + 10)):
            with pytest.raises(InsufficientDiskSpaceException):
                disk_space.ensure_free_space(chars * 1000, str(tmp_path), str(tmp_path))

    def test_missing_output_dir_falls_back_to_existing_parent(self, tmp_path):
        with patch.object(disk_space.shutil, "disk_usage", return_value=_Usage(10 * 1024 ** 4)):
            disk_space.ensure_free_space(10, str(tmp_path), str(tmp_path / "not" / "yet"))

    def test_estimate_scales_with_text_and_copies(self):
        assert disk_space.estimate_bytes(10, 2) == 2 * disk_space.estimate_bytes(10, 1)
        assert disk_space.estimate_bytes(0, 3) == 0

    def test_error_is_fatal_for_the_batch(self):
        from src.domain.exceptions import is_fatal_setup_error
        assert is_fatal_setup_error(InsufficientDiskSpaceException("x"))

    def _worker(self, tmp_path):
        src = tmp_path / "in.txt"
        src.write_text("Hello world. " * 50, encoding="utf-8")
        config = make_config(tmp_path, "Disk")
        import dataclasses
        config = dataclasses.replace(config, input_file_path=str(src))
        return PreparationWorker("tid", config, FileManager(), TextProcessor(), TextChunker())

    def test_preparation_fails_before_creating_directories_when_disk_is_full(self, tmp_path):
        worker = self._worker(tmp_path)
        errors, finished = [], []
        worker.error.connect(errors.append)
        worker.finished.connect(lambda *a: finished.append(a))
        with patch.object(disk_space.shutil, "disk_usage", return_value=_Usage(0)):
            worker.run()
        assert finished == [] and len(errors) == 1 and "disk space" in errors[0].lower()
        assert not list(tmp_path.glob("text_*")) and not list(tmp_path.glob("audio_*"))

    def test_preparation_succeeds_with_enough_space(self, tmp_path):
        worker = self._worker(tmp_path)
        errors, finished = [], []
        worker.error.connect(errors.append)
        worker.finished.connect(lambda *a: finished.append(a))
        with patch.object(disk_space.shutil, "disk_usage", return_value=_Usage(10 * 1024 ** 4)):
            worker.run()
        assert errors == [] and len(finished) == 1
