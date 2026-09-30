"""Regression tests for H5: XTTS/Piper must never run synthesis in parallel."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.domain.models import ProjectConfig, TtsBackend
from src.domain.xtts_audio_generator import XttsAudioGenerator
from src.domain.models import AudioChunk
from src.infrastructure.xtts_model_manager import XttsModelManager


def _config(backend, threads=4):
    d = tempfile.mkdtemp()
    f = Path(d) / "in.txt"
    f.write_text("hello")
    return ProjectConfig(
        project_name="p", input_file_path=str(f), language="en", gender="male",
        thread_count=threads, output_dir_path=d, tts_backend=backend,
    )


class TestEffectiveThreadCount(unittest.TestCase):

    def test_edge_keeps_user_value(self):
        self.assertEqual(_config(TtsBackend.EDGE_TTS, 4).effective_thread_count, 4)

    def test_piper_forced_to_one(self):
        self.assertEqual(_config(TtsBackend.PIPER, 4).effective_thread_count, 1)

    def test_xtts_forced_to_one(self):
        self.assertEqual(_config(TtsBackend.XTTS, 4).effective_thread_count, 1)


class TestGenerationServiceUsesEffectiveCount(unittest.TestCase):

    def _task(self, backend):
        from src.domain.models import GenerationTask
        task = MagicMock(spec=GenerationTask)
        task.config = _config(backend, 4)
        return task

    def test_xtts_thread_manager_is_single_worker(self):
        from src.application.services import generation_service as gs
        with patch.object(gs, "ThreadManager") as tm:
            svc = gs.GenerationService(MagicMock())
            try:
                svc.start_generation(self._task(TtsBackend.XTTS), [], "/tmp/out")
            except Exception:
                pass
            tm.assert_called_with(thread_count=1)

    def test_edge_thread_manager_uses_selected_count(self):
        from src.application.services import generation_service as gs
        with patch.object(gs, "ThreadManager") as tm:
            svc = gs.GenerationService(MagicMock())
            try:
                svc.start_generation(self._task(TtsBackend.EDGE_TTS), [], "/tmp/out")
            except Exception:
                pass
            tm.assert_called_with(thread_count=4)


class TestSynthesisIsSerialised(unittest.TestCase):

    def test_parallel_batches_never_overlap_in_model_tts(self):
        manager = MagicMock(spec=XttsModelManager)
        manager.get_reference_audio_path.return_value = Path("/tmp/s.wav")
        gen = XttsAudioGenerator(model_manager=manager)
        state = {"active": 0, "max": 0}
        guard = threading.Lock()

        def tts(**kw):
            with guard:
                state["active"] += 1
                state["max"] = max(state["max"], state["active"])
            time.sleep(0.03)
            with guard:
                state["active"] -= 1
            return [0.1] * 20

        gen._model = MagicMock()
        gen._model.tts.side_effect = tts
        gen._model_loaded = True

        # Plain assignment: patch.object is not thread-safe when entered from several threads.
        gen._convert_wav_bytes_to_mp3 = lambda _b, p: Path(p).write_bytes(b"x")
        gen._get_mp3_duration = lambda _p: 1.0

        def run(i):
            gen.generate_audio_batch(
                [AudioChunk(chunk_number=i, text_content=f"Hello {i}.")],
                "en", "male", tempfile.mkdtemp())

        threads = [threading.Thread(target=run, args=(i,)) for i in range(1, 5)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        self.assertEqual(gen._model.tts.call_count, 4)
        self.assertEqual(state["max"], 1)

    def test_unload_waits_for_running_synthesis(self):
        manager = MagicMock(spec=XttsModelManager)
        gen = XttsAudioGenerator(model_manager=manager)
        order = []
        started = threading.Event()

        gen._model = MagicMock()
        gen._model_loaded = True

        def worker():
            with gen._synth_lock:
                started.set()
                time.sleep(0.1)
                order.append("synth-done")

        t = threading.Thread(target=worker)
        t.start()
        started.wait(2)
        gen.unload_model()
        order.append("unloaded")
        t.join(2)
        self.assertEqual(order, ["synth-done", "unloaded"])
        self.assertFalse(gen.is_model_loaded)


if __name__ == "__main__":
    unittest.main()
