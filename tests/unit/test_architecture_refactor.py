"""
Regression tests for the architecture clean-up (audit section D):

layer boundaries, shared generator base, extracted collaborators, constants,
process registry and removed dead code.
"""

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("edge_tts", MagicMock())

from src import constants
from src.application import app_controller as controller_module
from src.application.backend_coordinator import BackendCoordinator
from src.application.progress_restorer import ProgressRestorer, load_chunks
from src.application.services.assembly_service import AssemblyService
from src.application.services.generation_service import GenerationService
from src.domain.async_loop_host import AsyncLoopHostMixin
from src.domain.audio_assembler import AudioAssembler
from src.domain.audio_generator import AudioGenerator
from src.domain.base_generator import BaseAudioGenerator
from src.domain.models import AudioChunk, TaskStatus, TtsBackend
from src.domain.piper_audio_generator import PiperAudioGenerator
from src.domain.process_registry import DEFAULT_PROCESS_REGISTRY, ProcessRegistry
from src.domain.text_chunker import TextChunker
from src.domain.tts_generator_protocol import TtsGeneratorProtocol
from src.domain.xtts_audio_generator import XttsAudioGenerator
from src.infrastructure.file_manager import FileManager

SRC = Path(__file__).resolve().parents[2] / "src"


def _src_text(*relative: str) -> str:
    return "\n".join((SRC / r).read_text(encoding="utf-8") for r in relative)


def _all_src_files():
    return [p for p in SRC.rglob("*.py")]


# ---------------------------------------------------------------------------
# D7: dead code stays removed
# ---------------------------------------------------------------------------

class TestDeadCodeRemoved:
    @pytest.mark.parametrize("name", [
        "generationCompleted", "_build_config_from_ui", "_run_piper_action", "_PiperActionWorker",
        "set_inputs_enabled", "def disable_all", "def check_docker", "def load_manifest",
        "_on_batch_generated", "_ACTIVE_PROCESSES", "_cleanup_processes", "config, 'voice_name'",
    ])
    def test_name_is_gone_from_src(self, name):
        offenders = [str(p.relative_to(SRC)) for p in _all_src_files()
                     if name in p.read_text(encoding="utf-8")]
        assert offenders == [], f"{name} reappeared in {offenders}"

    def test_live_helpers_that_looked_dead_remain(self):
        # get_container_logs is used by DockerManager diagnostics; is_model_loaded by the H8 UI.
        assert "get_container_logs" in _src_text("infrastructure/docker_manager.py")
        assert hasattr(XttsAudioGenerator, "is_model_loaded")


# ---------------------------------------------------------------------------
# D8: no historical fix markers in docstrings
# ---------------------------------------------------------------------------

class TestNoHistoricalMarkers:
    def test_no_bug_or_issue_markers(self):
        pattern = re.compile(r"\b(BUG|ISSUE)-\d+\b|\bSESSION-CLOSED FIX\b|\bFIX:")
        offenders = [str(p.relative_to(SRC)) for p in _all_src_files()
                     if pattern.search(p.read_text(encoding="utf-8"))]
        assert offenders == []


# ---------------------------------------------------------------------------
# D2: layer boundaries
# ---------------------------------------------------------------------------

class TestLayerBoundaries:
    PRIVATE_ACCESS = re.compile(
        r"\b_?(queue_service|generation_service|assembly_service|persistence_service|"
        r"audio_generator|thread_manager)\._[a-z]"
    )

    def test_no_private_member_of_another_layer_is_touched(self):
        offenders = []
        for p in (SRC / "application").rglob("*.py"):
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if self.PRIVATE_ACCESS.search(line) and "self._audio_generator._" not in line:
                    offenders.append(f"{p.name}:{n}: {line.strip()}")
        assert offenders == []

    def test_generation_service_exposes_worker_pool_state(self):
        service = GenerationService(MagicMock())
        assert service.has_worker_pool is False
        service._thread_manager = MagicMock()
        assert service.has_worker_pool is True


# ---------------------------------------------------------------------------
# D1: the controller is split into collaborators
# ---------------------------------------------------------------------------

class TestControllerDecomposition:
    def test_controller_shrank_below_limit(self):
        lines = (SRC / "application" / "app_controller.py").read_text(encoding="utf-8").count("\n")
        assert lines < 1100

    def test_preparation_worker_is_reexported(self):
        from src.application.preparation_worker import PreparationWorker
        assert controller_module.PreparationWorker is PreparationWorker

    def test_controller_does_not_branch_on_engine_for_lifecycle(self):
        text = (SRC / "application" / "app_controller.py").read_text(encoding="utf-8")
        assert "stop_container" not in text and "unload_model" not in text
        assert "ensure_ready" not in text

    def test_controller_keeps_compatibility_views_of_coordinator(self):
        from tests.unit._lifecycle_helpers import make_controller
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            c = make_controller(Path(tmp) / "state.json")[0]
            piper, xtts = MagicMock(), MagicMock()
            c._piper_setup_service = piper
            c._xtts_setup_service = xtts
            assert c._backends.service(TtsBackend.PIPER) is piper
            assert c._backends.service(TtsBackend.XTTS) is xtts
            c._piper_used = True
            assert TtsBackend.PIPER in c._backends.used and c._piper_used
            c._piper_used = False
            assert not c._piper_used
            c._backends_needed.add(TtsBackend.XTTS)
            assert TtsBackend.XTTS in c._backends.needed
            c._piper_setup_service = None
            assert c._piper_setup_service is None


# ---------------------------------------------------------------------------
# D1: BackendCoordinator
# ---------------------------------------------------------------------------

class TestBackendCoordinator:
    def _coord(self):
        piper, xtts = MagicMock(), MagicMock()
        xtts.is_model_loaded.return_value = True
        return BackendCoordinator(piper, xtts), piper, xtts

    @staticmethod
    def _inline(fn):
        fn()

    def test_no_preparation_for_edge(self):
        coord, *_ = self._coord()
        assert coord.prepare_callable(TtsBackend.EDGE_TTS, "en", "male") is None

    def test_no_preparation_without_service(self):
        assert BackendCoordinator().prepare_callable(TtsBackend.PIPER, "en", "male") is None

    def test_prepare_calls_ensure_ready_of_the_right_service(self):
        coord, piper, xtts = self._coord()
        piper.ensure_ready.return_value = "ok"
        assert coord.prepare_callable(TtsBackend.PIPER, "uk", "female")() == "ok"
        piper.ensure_ready.assert_called_once_with("uk", "female")
        xtts.ensure_ready.assert_not_called()

    def test_prepare_errors_propagate(self):
        coord, piper, _ = self._coord()
        piper.ensure_ready.side_effect = RuntimeError("docker down")
        with pytest.raises(RuntimeError, match="docker down"):
            coord.prepare_callable(TtsBackend.PIPER, "en", "male")()

    def test_token_invalidation(self):
        coord, *_ = self._coord()
        token = coord.begin(TtsBackend.PIPER)
        assert coord.is_current(token)
        coord.invalidate()
        assert not coord.is_current(token)
        assert coord.begin(TtsBackend.XTTS) != token

    def test_begin_marks_backend_needed_and_used(self):
        coord, *_ = self._coord()
        coord.begin(TtsBackend.PIPER)
        assert TtsBackend.PIPER in coord.needed and TtsBackend.PIPER in coord.used

    def test_piper_is_stopped_only_if_this_run_used_it(self):
        coord, piper, _ = self._coord()
        coord.release_idle(lambda b: False, self._inline)
        piper.stop_container.assert_not_called()
        coord.begin(TtsBackend.PIPER)
        coord.release_idle(lambda b: False, self._inline)
        piper.stop_container.assert_called_once()
        assert TtsBackend.PIPER not in coord.used

    def test_unfinished_task_keeps_its_backend(self):
        coord, piper, xtts = self._coord()
        coord.begin(TtsBackend.PIPER)
        coord.release_idle(lambda b: b in (TtsBackend.PIPER, TtsBackend.XTTS), self._inline)
        piper.stop_container.assert_not_called()
        xtts.unload_model.assert_not_called()

    def test_xtts_unloaded_only_when_loaded(self):
        coord, _, xtts = self._coord()
        xtts.is_model_loaded.return_value = False
        coord.release_idle(lambda b: False, self._inline)
        xtts.unload_model.assert_not_called()
        xtts.is_model_loaded.return_value = True
        coord.release_idle(lambda b: False, self._inline)
        xtts.unload_model.assert_called_once()

    def test_backend_started_meanwhile_is_not_released(self):
        coord, piper, _ = self._coord()
        coord.begin(TtsBackend.PIPER)
        deferred = []
        coord.release_idle(lambda b: False, deferred.append)
        coord.begin(TtsBackend.PIPER)            # next task starts before the release thread runs
        deferred[0]()
        piper.stop_container.assert_not_called()

    def test_release_failure_is_logged_not_raised(self):
        coord, piper, xtts = self._coord()
        piper.stop_container.side_effect = RuntimeError("boom")
        coord.begin(TtsBackend.PIPER)
        coord.release_idle(lambda b: False, self._inline)
        xtts.unload_model.assert_called_once()   # the other engine is still released

    def test_shutdown_releases_everything_and_survives_errors(self):
        coord, piper, xtts = self._coord()
        piper.stop_container.side_effect = RuntimeError("boom")
        coord.shutdown()
        piper.stop_container.assert_called_once()
        xtts.unload_model.assert_called_once()

    def test_shutdown_skips_unloaded_xtts(self):
        coord, _, xtts = self._coord()
        xtts.is_model_loaded.return_value = False
        coord.shutdown()
        xtts.unload_model.assert_not_called()

    def test_preparations_are_serialised_by_the_lock(self):
        coord, piper, _ = self._coord()
        seen = []
        piper.ensure_ready.side_effect = lambda *a: seen.append(coord.lock.locked())
        coord.prepare_callable(TtsBackend.PIPER, "en", "male")()
        assert seen == [True]


# ---------------------------------------------------------------------------
# D1: ProgressRestorer / load_chunks
# ---------------------------------------------------------------------------

class TestProgressRestorer:
    def _dirs(self, tmp_path, n=3):
        text, audio = tmp_path / "text", tmp_path / "audio"
        text.mkdir(), audio.mkdir()
        for i in range(1, n + 1):
            (text / f"{i}.txt").write_text(f"chunk {i}", encoding="utf-8")
        return text, audio

    def test_chunks_are_loaded_in_numeric_order(self, tmp_path):
        text, _ = self._dirs(tmp_path, 12)
        assert [c.chunk_number for c in load_chunks(str(text))] == list(range(1, 13))

    def test_non_numeric_files_are_ignored(self, tmp_path):
        text, _ = self._dirs(tmp_path, 2)
        (text / "notes.txt").write_text("x")
        assert len(load_chunks(str(text))) == 2

    def test_empty_or_missing_directory_gives_none(self, tmp_path):
        (tmp_path / "t").mkdir()
        assert load_chunks(str(tmp_path / "t")) is None
        assert load_chunks(str(tmp_path / "missing")) is None

    def test_unreadable_chunk_aborts_restore(self, tmp_path):
        text, _ = self._dirs(tmp_path, 2)
        (text / "2.txt").write_bytes(b"\xff\xfe\xfa")
        assert load_chunks(str(text)) is None

    def test_chunk_text_is_preserved_with_path(self, tmp_path):
        text, _ = self._dirs(tmp_path, 1)
        chunk = load_chunks(str(text))[0]
        assert chunk.text_content == "chunk 1" and chunk.text_file_path.endswith("1.txt")

    def _restorer(self, durations):
        assembly = MagicMock()
        assembly.BATCH_SIZE = 2
        assembly.mark_chunk_ready.side_effect = lambda idx, dur: (idx // 2) if idx % 2 == 1 else None
        return ProgressRestorer(assembly, lambda p: durations.get(Path(p).name, 0.0)), assembly

    def test_valid_audio_is_registered(self, tmp_path):
        text, audio = self._dirs(tmp_path, 3)
        (audio / "1.mp3").write_bytes(b"x")
        restorer, assembly = self._restorer({"1.mp3": 2.0})
        result = restorer.restore(load_chunks(str(text)), str(audio), 1.0, "t")
        assert result.audio_files[0] == str((audio / "1.mp3").absolute())
        assert result.audio_files[1:] == [None, None]
        assert result.chunks[0].audio_file_path and result.chunks[1].audio_file_path is None
        assembly.reset.assert_called_once_with(3)

    def test_corrupt_chunk_audio_is_deleted_and_not_counted(self, tmp_path):
        text, audio = self._dirs(tmp_path, 2)
        (audio / "1.mp3").write_bytes(b"x")
        restorer, _ = self._restorer({})
        result = restorer.restore(load_chunks(str(text)), str(audio), 1.0, "t")
        assert result.audio_files == [None, None] and not (audio / "1.mp3").exists()

    def test_empty_audio_file_is_skipped(self, tmp_path):
        text, audio = self._dirs(tmp_path, 1)
        (audio / "1.mp3").write_bytes(b"")
        restorer, _ = self._restorer({"1.mp3": 5.0})
        assert restorer.restore(load_chunks(str(text)), str(audio), 1.0, "t").audio_files == [None]

    def test_finished_batch_with_valid_part_is_restored(self, tmp_path):
        text, audio = self._dirs(tmp_path, 2)
        for n in ("1.mp3", "2.mp3", "part_0.mp3"):
            (audio / n).write_bytes(b"x")
        restorer, assembly = self._restorer({"1.mp3": 1.0, "2.mp3": 1.0, "part_0.mp3": 2.0})
        restorer.restore(load_chunks(str(text)), str(audio), 1.0, "t")
        assembly.restore_batch_result.assert_called_once_with(0, str(audio / "part_0.mp3"))
        assembly.submit_batch_by_index.assert_not_called()

    def test_finished_batch_without_part_is_reassembled(self, tmp_path):
        text, audio = self._dirs(tmp_path, 2)
        for n in ("1.mp3", "2.mp3"):
            (audio / n).write_bytes(b"x")
        restorer, assembly = self._restorer({"1.mp3": 1.0, "2.mp3": 1.0})
        restorer.restore(load_chunks(str(text)), str(audio), 1.25, "tid")
        args = assembly.submit_batch_by_index.call_args[0]
        assert args[0] == 0 and len(args[1]) == 2 and args[3] == 1.25 and args[4] == "tid"

    def test_corrupt_part_is_removed_and_batch_reassembled(self, tmp_path):
        text, audio = self._dirs(tmp_path, 2)
        for n in ("1.mp3", "2.mp3", "part_0.mp3"):
            (audio / n).write_bytes(b"x")
        restorer, assembly = self._restorer({"1.mp3": 1.0, "2.mp3": 1.0})   # part has 0 duration
        restorer.restore(load_chunks(str(text)), str(audio), 1.0, "t")
        assert not (audio / "part_0.mp3").exists()
        assembly.submit_batch_by_index.assert_called_once()
        assembly.restore_batch_result.assert_not_called()

    def test_input_chunk_list_is_not_mutated(self, tmp_path):
        text, audio = self._dirs(tmp_path, 1)
        (audio / "1.mp3").write_bytes(b"x")
        chunks = load_chunks(str(text))
        restorer, _ = self._restorer({"1.mp3": 1.0})
        restorer.restore(chunks, str(audio), 1.0, "t")
        assert chunks[0].audio_file_path is None


# ---------------------------------------------------------------------------
# D3/D4: shared generator base and protocol
# ---------------------------------------------------------------------------

def _xtts():
    return XttsAudioGenerator(model_manager=MagicMock())


class TestGeneratorBaseAndProtocol:
    @pytest.mark.parametrize("factory", [AudioGenerator, PiperAudioGenerator, _xtts])
    def test_every_generator_satisfies_the_protocol(self, factory):
        gen = factory()
        try:
            assert isinstance(gen, TtsGeneratorProtocol)
            assert isinstance(gen, BaseAudioGenerator)
        finally:
            gen.close()

    @pytest.mark.parametrize("factory", [AudioGenerator, PiperAudioGenerator])
    def test_cloud_and_docker_engines_have_noop_model_lifecycle(self, factory):
        gen = factory()
        try:
            assert gen.is_model_loaded is False
            gen.unload_model()
            assert gen.is_model_loaded is False
        finally:
            gen.close()

    def test_no_generator_redefines_shared_helpers(self):
        for name in ("audio_generator.py", "piper_audio_generator.py", "xtts_audio_generator.py"):
            text = _src_text(f"domain/{name}")
            for helper in ("def _cleanup_tmp_files", "def _get_mp3_duration",
                           "def _get_file_duration_fast", "def generate_audio(",
                           "def _convert_wav_to_mp3"):
                assert helper not in text, f"{helper} duplicated in {name}"

    def test_generate_audio_delegates_to_batch(self):
        class Gen(BaseAudioGenerator):
            def generate_audio_batch(self, chunks, language, gender, output_dir, **kw):
                return [("/x/%d.mp3" % c.chunk_number, 1.5) for c in chunks]

        assert Gen().generate_audio(AudioChunk(7, "t"), "en", "male", "/o") == ("/x/7.mp3", 1.5)

    def test_cleanup_tmp_files_ignores_none_and_missing(self, tmp_path):
        f = tmp_path / "a.tmp"
        f.write_text("x")
        BaseAudioGenerator._cleanup_tmp_files(f, None, tmp_path / "missing")
        assert not f.exists()

    def test_duration_of_unreadable_file_is_zero(self, tmp_path):
        bad = tmp_path / "x.mp3"
        bad.write_bytes(b"not an mp3")
        assert BaseAudioGenerator()._get_mp3_duration(str(bad)) == 0.0
        assert BaseAudioGenerator()._get_file_duration_fast(str(tmp_path / "none.mp3")) == 0.0

    def test_wav_source_is_removed_even_if_conversion_fails(self, tmp_path):
        wav = tmp_path / "a.wav"
        wav.write_bytes(b"not a wav")
        with pytest.raises(Exception):
            BaseAudioGenerator()._convert_wav_to_mp3(wav, tmp_path / "a.mp3.tmp")
        assert not wav.exists()

    def test_xtts_keeps_its_own_bitrate(self):
        from src.domain.xtts_audio_generator import MP3_BITRATE
        assert XttsAudioGenerator.MP3_BITRATE == MP3_BITRATE
        assert PiperAudioGenerator.MP3_BITRATE == BaseAudioGenerator.MP3_BITRATE

    def test_pause_and_resume_are_reflected(self):
        gen = PiperAudioGenerator()
        try:
            gen.pause()
            assert gen.is_paused()
            gen.resume()
            assert not gen.is_paused()
        finally:
            gen.close()


# ---------------------------------------------------------------------------
# D4: shared asyncio loop host
# ---------------------------------------------------------------------------

class TestAsyncLoopHost:
    def test_edge_and_piper_use_the_shared_host(self):
        assert issubclass(AudioGenerator, AsyncLoopHostMixin)
        assert issubclass(PiperAudioGenerator, AsyncLoopHostMixin)
        for name in ("audio_generator.py", "piper_audio_generator.py"):
            text = _src_text(f"domain/{name}")
            assert "def ensure_loop_running" not in text and "new_event_loop" not in text

    def test_thread_names_identify_the_owner(self):
        a, p = AudioGenerator(), PiperAudioGenerator()
        try:
            assert a._loop_thread.name == "AudioGenerator-AsyncLoop"
            assert p._loop_thread.name == "PiperAudioGenerator-AsyncLoop"
        finally:
            a.close(), p.close()

    def test_loop_primitives_are_created_inside_the_loop(self):
        a, p = AudioGenerator(max_concurrency=4), PiperAudioGenerator()
        try:
            assert a._semaphore is not None and a._network_lock is not None
            assert p._semaphore is not None
        finally:
            a.close(), p.close()

    def test_dead_loop_is_restarted(self):
        p = PiperAudioGenerator()
        try:
            old = p._loop
            p.close()
            p.ensure_loop_running()
            assert p._loop is not old and p._loop.is_running() and p._loop_thread.is_alive()
        finally:
            p.close()

    def test_close_is_idempotent(self):
        a = AudioGenerator()
        a.close()
        a.close()

    def test_running_loop_is_not_replaced(self):
        a = AudioGenerator()
        try:
            loop = a._loop
            a.ensure_loop_running()
            assert a._loop is loop
        finally:
            a.close()

    def test_loop_start_timeout_raises(self):
        class Stuck(AsyncLoopHostMixin):
            def __init__(self):
                self._init_loop_host()

            def _start_background_loop(self, loop):   # never signals readiness
                pass

        with patch("src.domain.async_loop_host.LOOP_START_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(RuntimeError, match="did not start"):
                Stuck().ensure_loop_running()


# ---------------------------------------------------------------------------
# D5: process registry instead of a module-level mutable set
# ---------------------------------------------------------------------------

class TestProcessRegistry:
    def test_add_discard_len(self):
        reg, proc = ProcessRegistry(), MagicMock()
        reg.add(proc)
        assert len(reg) == 1
        reg.discard(proc)
        reg.discard(proc)
        assert len(reg) == 0

    def test_kill_all_kills_and_survives_errors(self):
        reg = ProcessRegistry()
        good, bad = MagicMock(), MagicMock()
        bad.kill.side_effect = OSError("gone")
        reg.add(bad), reg.add(good)
        reg.kill_all()
        good.kill.assert_called_once() and bad.kill.assert_called_once()

    def test_assembler_registers_and_unregisters_its_process(self):
        reg = ProcessRegistry()
        assembler = AudioAssembler(process_registry=reg)
        proc = MagicMock()
        proc.returncode = 0
        proc.poll.return_value = 0
        seen = []
        with patch("src.domain.audio_assembler.subprocess.Popen", return_value=proc), \
                patch.object(assembler, "_monitor_process", side_effect=lambda *a: seen.append(len(reg)) or ""):
            assembler._execute_ffmpeg(["ffmpeg"], 1.0, 1.0, None)
        assert seen == [1] and len(reg) == 0

    def test_empty_injected_registry_is_not_replaced_by_default(self):
        reg = ProcessRegistry()          # len() == 0 is falsy; must still be honoured
        assert AudioAssembler(process_registry=reg)._registry is reg

    def test_default_registry_is_used_otherwise(self):
        assert AudioAssembler()._registry is DEFAULT_PROCESS_REGISTRY

    def test_registry_is_cleaned_when_ffmpeg_fails(self):
        reg = ProcessRegistry()
        assembler = AudioAssembler(process_registry=reg)
        proc = MagicMock()
        proc.returncode = 1
        proc.poll.return_value = 1
        with patch("src.domain.audio_assembler.subprocess.Popen", return_value=proc), \
                patch.object(assembler, "_monitor_process", return_value="bad"):
            with pytest.raises(Exception, match="exited with code 1"):
                assembler._execute_ffmpeg(["ffmpeg"], 1.0, 1.0, None)
        assert len(reg) == 0


# ---------------------------------------------------------------------------
# D6: magic numbers have names
# ---------------------------------------------------------------------------

class TestConstants:
    def test_values_used_by_the_pipeline(self):
        assert AssemblyService.BATCH_SIZE == constants.ASSEMBLY_BATCH_SIZE == 40
        assert GenerationService.BATCH_GEN_SIZE == constants.GENERATION_BATCH_SIZE == 10
        assert controller_module.ApplicationController.MAX_RETRY_SWEEPS == constants.MAX_RETRY_SWEEPS == 5
        assert controller_module.ApplicationController.RETRY_SWEEP_DELAY_MS == constants.RETRY_SWEEP_DELAY_MS
        chunks = TextChunker().chunk_text("word " * 600)
        assert max(len(c.text_content) for c in chunks) <= constants.CHUNK_TARGET_CHARS * 1.3

    def test_chunker_default_uses_the_constant(self):
        import inspect
        assert inspect.signature(TextChunker.chunk_text).parameters["chunk_size"].default \
            == constants.CHUNK_TARGET_CHARS

    @pytest.mark.parametrize("path,literal", [
        ("application/app_controller.py", "start(500)"),
        ("application/app_controller.py", "start(1000)"),
        ("domain/audio_generator.py", "timeout=60"),
        ("domain/audio_assembler.py", "> 180.0"),
        ("domain/xtts_audio_generator.py", "0.2 * XTTS_SAMPLE_RATE"),
        ("application/services/assembly_service.py", "BATCH_SIZE = 40"),
        ("application/services/generation_service.py", "BATCH_GEN_SIZE = 10"),
    ])
    def test_literal_is_not_inlined(self, path, literal):
        assert literal not in _src_text(path)

    def test_constants_are_positive(self):
        for name in dir(constants):
            if name.isupper():
                assert getattr(constants, name) > 0, name


# ---------------------------------------------------------------------------
# E5: FileManager does not cache stale paths
# ---------------------------------------------------------------------------

class TestFileManagerNoStaleCache:
    def test_no_lru_cache_on_instance_methods(self):
        assert "lru_cache" not in _src_text("infrastructure/file_manager.py")
        assert not hasattr(FileManager._get_path, "cache_info")

    def test_directory_recreated_between_calls_is_seen(self, tmp_path):
        fm = FileManager()
        parent = tmp_path / "work"
        parent.mkdir()
        first = fm.create_timestamped_dir("a", str(parent))
        assert Path(first).is_dir()
        parent.rmdir() if not any(parent.iterdir()) else __import__("shutil").rmtree(parent)
        with pytest.raises(ValueError):
            fm.create_timestamped_dir("a", str(parent))
        parent.mkdir()
        assert Path(fm.create_timestamped_dir("a", str(parent))).is_dir()

    def test_symlink_retargeting_is_followed(self, tmp_path):
        a, b, link = tmp_path / "a", tmp_path / "b", tmp_path / "link"
        a.mkdir(), b.mkdir()
        link.symlink_to(a)
        fm = FileManager()
        assert Path(fm.create_timestamped_dir("x", str(link))).parent == a
        link.unlink()
        link.symlink_to(b)
        assert Path(fm.create_timestamped_dir("x", str(link))).parent == b

    def test_file_manager_instances_do_not_leak_through_a_cache(self):
        import gc, weakref
        fm = FileManager()
        fm._get_path("/tmp")
        ref = weakref.ref(fm)
        del fm
        gc.collect()
        assert ref() is None
