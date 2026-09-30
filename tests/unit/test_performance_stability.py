"""
Regression tests for audit section E (performance and stability):

shared retry budget, request-concurrency planning, XTTS conditioning-latent cache and
streaming preparation.
"""

import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

sys.modules.setdefault("edge_tts", MagicMock())

from src import constants
from src.application import preparation_worker as prep
from src.application.preparation_worker import PreparationWorker
from src.application.services.generation_service import GenerationService, plan_concurrency
from src.domain.base_generator import BaseAudioGenerator
from src.domain.exceptions import PiperConnectionException
from src.domain.models import AudioChunk
from src.domain.piper_audio_generator import PiperAudioGenerator
from src.domain.retry_budget import MIN_RETRY_BUDGET, RETRIES_PER_CHUNK, RetryBudget
from src.domain.xtts_audio_generator import XttsAudioGenerator
from src.infrastructure.xtts_model_manager import XttsModelManager


def chunk(n, text="Hello there"):
    return AudioChunk(chunk_number=n, text_content=text)


# ---------------------------------------------------------------------------
# RetryBudget
# ---------------------------------------------------------------------------

class TestRetryBudget:
    def test_consumes_until_limit_then_refuses(self):
        b = RetryBudget(3)
        assert [b.try_consume() for _ in range(5)] == [True, True, True, False, False]
        assert b.used == 3 and b.exhausted

    def test_refused_attempts_are_not_counted(self):
        b = RetryBudget(1)
        b.try_consume()
        b.try_consume()
        assert b.used == 1

    def test_zero_and_negative_limits_are_exhausted_immediately(self):
        assert RetryBudget(0).exhausted and not RetryBudget(0).try_consume()
        assert RetryBudget(-5).limit == 0

    def test_for_chunks_has_a_floor_and_scales(self):
        assert RetryBudget.for_chunks(0).limit == MIN_RETRY_BUDGET
        assert RetryBudget.for_chunks(1).limit == MIN_RETRY_BUDGET
        big = MIN_RETRY_BUDGET * 10
        assert RetryBudget.for_chunks(big).limit == big * RETRIES_PER_CHUNK

    def test_thread_safe_never_overspends(self):
        b = RetryBudget(100)
        granted = []

        def worker():
            granted.extend(b.try_consume() for _ in range(50))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sum(granted) == 100 and b.used == 100


class TestBaseGeneratorBudget:
    class _G(BaseAudioGenerator):
        def generate_audio_batch(self, *a, **k):
            return []

    def test_unlimited_without_budget(self):
        g = self._G()
        assert all(g._may_retry() for _ in range(1000))

    def test_budget_is_honoured_and_can_be_cleared(self):
        g = self._G()
        g.set_retry_budget(RetryBudget(2))
        assert [g._may_retry() for _ in range(3)] == [True, True, False]
        g.set_retry_budget(None)
        assert g._may_retry()


class TestPiperHonoursBudget:
    def _run(self, budget, max_retries=10):
        gen = PiperAudioGenerator(host="127.0.0.1", port=10200)
        gen.set_retry_budget(budget)
        try:
            with patch.object(gen, "_synthesise_to_wav", new_callable=AsyncMock) as synth, \
                    patch("src.domain.piper_audio_generator.asyncio.sleep", new=AsyncMock()):
                synth.side_effect = PiperConnectionException("truncated")
                result = gen.generate_audio_batch(
                    [chunk(1)], "ru", "male", "/tmp", max_retries=max_retries, backoff=0.0
                )
            return result, synth.call_count
        finally:
            gen.close()

    def test_exhausted_budget_stops_after_first_attempt(self):
        b = RetryBudget(0)
        result, calls = self._run(b)
        assert result == [None] and calls == 1

    def test_small_budget_limits_total_attempts(self):
        b = RetryBudget(2)
        _, calls = self._run(b)
        assert calls == 3 and b.used == 2

    def test_without_budget_full_retries_are_used(self):
        _, calls = self._run(None, max_retries=4)
        assert calls == 4


class TestServiceInstallsBudget:
    def _start(self, chunks):
        gen = MagicMock(spec=BaseAudioGenerator)
        gen.last_error = None
        service = GenerationService(gen)
        task = MagicMock()
        task.config = MagicMock(language="en", gender="male", effective_thread_count=1)
        with patch("src.application.services.generation_service.ThreadManager"):
            service.start_generation(task, chunks, "/out")
        return gen

    def test_budget_is_installed_per_generation(self):
        gen = self._start([chunk(i) for i in range(1, 4)])
        budget = gen.set_retry_budget.call_args[0][0]
        assert isinstance(budget, RetryBudget) and budget.limit == MIN_RETRY_BUDGET

    def test_budget_scales_with_pending_chunks_only(self):
        n = 100
        chunks = [chunk(i) for i in range(1, n + 1)]
        done = [AudioChunk(chunk_number=1000 + i, text_content="x", audio_file_path=f"/a/{i}.mp3")
                for i in range(50)]
        gen = self._start(chunks + done)
        assert gen.set_retry_budget.call_args[0][0].limit == n * RETRIES_PER_CHUNK

    def test_each_generation_gets_a_fresh_budget(self):
        gen = MagicMock(spec=BaseAudioGenerator)
        gen.last_error = None
        service = GenerationService(gen)
        task = MagicMock()
        task.config = MagicMock(language="en", gender="male", effective_thread_count=1)
        with patch("src.application.services.generation_service.ThreadManager"):
            service.start_generation(task, [chunk(1)], "/out")
            service.start_generation(task, [chunk(1)], "/out")
        budgets = [c[0][0] for c in gen.set_retry_budget.call_args_list]
        assert budgets[0] is not budgets[1]


# ---------------------------------------------------------------------------
# Concurrency planning
# ---------------------------------------------------------------------------

class TestPlanConcurrency:
    @pytest.mark.parametrize("threads,batches,expected", [
        (1, 1, (1, 1)),
        (1, 50, (1, 1)),
        (4, 50, (4, 1)),
        (4, 2, (2, 2)),
        (4, 1, (1, 4)),
        (10, 3, (3, 3)),
        (0, 5, (1, 1)),
        (-3, 5, (1, 1)),
        (4, 0, (1, 4)),
    ])
    def test_table(self, threads, batches, expected):
        assert plan_concurrency(threads, batches) == expected

    def test_product_never_exceeds_requested_concurrency(self):
        for threads in range(1, 15):
            for batches in range(0, 30):
                p, w = plan_concurrency(threads, batches)
                assert p >= 1 and w >= 1
                assert p * w <= max(1, min(threads, constants.MAX_PARALLEL_BATCHES))

    def test_capped_by_max_parallel_batches(self):
        p, w = plan_concurrency(1000, 1000)
        assert p == constants.MAX_PARALLEL_BATCHES and w == 1

    @pytest.mark.parametrize("threads,n_chunks,pool,workers", [
        (4, 1, 4, 4),
        (4, 100, 4, 1),
        (1, 100, 1, 1),
    ])
    def test_service_passes_planned_workers_to_generator(self, threads, n_chunks, pool, workers):
        gen = MagicMock(spec=BaseAudioGenerator)
        gen.last_error = None
        service = GenerationService(gen)
        task = MagicMock()
        task.config = MagicMock(language="en", gender="male", effective_thread_count=threads)
        task.id = "t"
        with patch("src.application.services.generation_service.ThreadManager") as tm:
            service.start_generation(task, [chunk(i) for i in range(1, n_chunks + 1)], "/out")
        assert tm.call_args.kwargs["thread_count"] == pool
        submit_args = tm.return_value.submit_task.call_args
        assert workers in submit_args.args or workers in submit_args.kwargs.values()


# ---------------------------------------------------------------------------
# XTTS conditioning-latent cache
# ---------------------------------------------------------------------------

class _FakeTtsModel:
    def __init__(self):
        self.latent_calls = 0
        self.inference_calls = []

    def get_conditioning_latents(self, audio_path):
        self.latent_calls += 1
        return f"gpt{self.latent_calls}", f"spk{self.latent_calls}"

    def inference(self, text, language, gpt, spk):
        self.inference_calls.append((text, language, gpt, spk))
        return {"wav": np.ones(10, dtype=np.float32)}


def _model_with(tts_model):
    model = MagicMock()
    model.synthesizer.tts_model = tts_model
    return model


@pytest.fixture
def gen():
    return XttsAudioGenerator(model_manager=MagicMock(spec=XttsModelManager))


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "ref.wav"
    p.write_bytes(b"RIFF0000")
    return str(p)


class TestXttsLatentCache:
    def test_latents_computed_once_for_many_segments(self, gen, wav):
        tm = _FakeTtsModel()
        model = _model_with(tm)
        for i in range(5):
            gen._run_inference(model, f"t{i}", "en", wav)
        assert tm.latent_calls == 1
        assert len(tm.inference_calls) == 5
        assert all(c[2:] == ("gpt1", "spk1") for c in tm.inference_calls)
        model.tts.assert_not_called()

    def test_changed_reference_file_recomputes(self, gen, wav):
        tm = _FakeTtsModel()
        model = _model_with(tm)
        gen._run_inference(model, "a", "en", wav)
        Path(wav).write_bytes(b"RIFF0000-longer-content")
        gen._run_inference(model, "b", "en", wav)
        assert tm.latent_calls == 2
        assert tm.inference_calls[-1][2] == "gpt2"

    def test_different_speaker_recomputes_and_keeps_single_entry(self, gen, wav, tmp_path):
        other = tmp_path / "other.wav"
        other.write_bytes(b"RIFF1111")
        tm = _FakeTtsModel()
        model = _model_with(tm)
        gen._run_inference(model, "a", "en", wav)
        gen._run_inference(model, "b", "en", str(other))
        assert tm.latent_calls == 2
        assert len(gen._latents_cache) == 1

    def test_unload_clears_the_cache(self, gen, wav):
        tm = _FakeTtsModel()
        model = _model_with(tm)
        gen._run_inference(model, "a", "en", wav)
        gen._model = model
        gen._model_loaded = True
        gen.unload_model()
        assert gen._latents_cache == {}
        gen._run_inference(_model_with(tm), "b", "en", wav)
        assert tm.latent_calls == 2

    def test_falls_back_to_model_tts_when_low_level_api_missing(self, gen, wav):
        model = MagicMock()
        model.synthesizer.tts_model = object()  # no get_conditioning_latents
        model.tts.return_value = [0.1, 0.2]
        assert gen._run_inference(model, "hi", "en", wav) == [0.1, 0.2]
        model.tts.assert_called_once_with(text="hi", speaker_wav=wav, language="en")

    def test_fallback_is_remembered(self, gen, wav):
        model = MagicMock()
        model.synthesizer.tts_model = object()
        gen._run_inference(model, "a", "en", wav)
        gen._run_inference(model, "b", "en", wav)
        assert gen._latents_unsupported is True
        assert model.tts.call_count == 2

    def test_unsupported_flag_reset_on_unload(self, gen, wav):
        model = MagicMock()
        model.synthesizer.tts_model = object()
        gen._run_inference(model, "a", "en", wav)
        gen._model = model
        gen._model_loaded = True
        gen.unload_model()
        assert gen._latents_unsupported is False

    def test_tensor_output_converted_to_numpy(self, gen, wav):
        class Tensor:
            def detach(self):
                return self

            def cpu(self):
                return self

            def numpy(self):
                return np.zeros(3, dtype=np.float32)

        tm = _FakeTtsModel()
        tm.inference = lambda *a: {"wav": Tensor()}
        out = gen._run_inference(_model_with(tm), "a", "en", wav)
        assert isinstance(out, np.ndarray) and len(out) == 3

    def test_works_without_torch(self, gen, wav):
        tm = _FakeTtsModel()
        with patch.dict(sys.modules, {"torch": None}):
            out = gen._run_inference(_model_with(tm), "a", "en", wav)
        assert len(out) == 10

    def test_missing_reference_file_does_not_crash_key(self, gen):
        assert gen._speaker_key("/nonexistent/ref.wav") == ("/nonexistent/ref.wav", 0, 0)


# ---------------------------------------------------------------------------
# PreparationWorker streaming
# ---------------------------------------------------------------------------

class _Processor:
    def process_text(self, text):
        return text


class _Chunker:
    def __init__(self, size=10):
        self.size = size

    def chunk_text(self, text):
        words, out, cur = text.split(), [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > self.size:
                out.append(AudioChunk(chunk_number=1, text_content=cur))
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            out.append(AudioChunk(chunk_number=1, text_content=cur))
        return out


def _worker(path, chunker=None):
    config = MagicMock(input_file_path=str(path), output_dir_path=str(path.parent))
    return PreparationWorker("tid", config, MagicMock(), _Processor(), chunker or _Chunker())


class TestPreparationStreaming:
    def test_blocks_never_split_words_and_reassemble(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prep, "READ_BLOCK_BYTES", 16)
        text = " ".join(f"word{i}" for i in range(40))
        f = tmp_path / "a.txt"
        f.write_text(text, encoding="utf-8")
        blocks = list(_worker(f)._iter_text_blocks())
        assert len(blocks) > 1
        assert " ".join(b.strip() for b in blocks).split() == text.split()

    def test_numbering_is_global_across_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prep, "READ_BLOCK_BYTES", 16)
        f = tmp_path / "a.txt"
        f.write_text(" ".join(f"w{i}" for i in range(60)), encoding="utf-8")
        chunks = _worker(f)._read_and_chunk()
        assert [c.chunk_number for c in chunks] == list(range(1, len(chunks) + 1))
        assert len(chunks) > 3

    def test_unicode_survives_block_boundaries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prep, "READ_BLOCK_BYTES", 8)
        text = "привіт світе їжак єнот ґанок " * 5
        f = tmp_path / "u.txt"
        f.write_text(text, encoding="utf-8")
        chunks = _worker(f, _Chunker(1000))._read_and_chunk()
        assert " ".join(c.text_content for c in chunks).split() == text.split()

    def test_empty_and_whitespace_files_yield_no_chunks(self, tmp_path):
        for content in ("", "   \n\n  "):
            f = tmp_path / "e.txt"
            f.write_text(content, encoding="utf-8")
            assert _worker(f)._read_and_chunk() == []

    def test_empty_file_emits_error(self, tmp_path):
        f = tmp_path / "e.txt"
        f.write_text("", encoding="utf-8")
        w = _worker(f)
        errors = []
        w.error.connect(errors.append)
        w.run()
        assert errors and "No text chunks" in errors[0]

    def test_interruption_stops_reading(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("some text here", encoding="utf-8")
        w = _worker(f)
        w.isInterruptionRequested = lambda: True
        assert w._read_and_chunk() is None

    def test_missing_file_emits_error(self, tmp_path):
        w = _worker(tmp_path / "missing.txt")
        errors = []
        w.error.connect(errors.append)
        w.run()
        assert len(errors) == 1
