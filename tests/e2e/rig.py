"""
End-to-end rig: the real application (Container, controller, services, generators,
ffmpeg assembly and the PyQt6 window) wired exactly like ``main.main()``, with only the
outermost engine boundaries faked:

* Edge TTS   -> ``FakeEdgeTts`` replaces the ``edge_tts`` module (no network).
* Piper      -> ``FakeWyomingServer`` is a real Wyoming TCP server; ``FakeDocker`` starts
                and stops it instead of ``docker compose``.
* XTTS-v2    -> ``FakeGpu`` provides ``torch`` and a ``TTS`` package whose model "clones"
                the dominant frequency of the speaker reference WAV, so the voice used
                for every segment can be read back from the final MP3.

Every engine renders sine tones, so the produced MP3 files are real audio whose length and
pitch can be analysed.
"""

import asyncio
import gc
import socket
import subprocess
import sys
import threading
import time
import types
import weakref
import zlib
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
from PyQt6.QtWidgets import QApplication

from src.domain.models import TaskStatus, TtsBackend

SECONDS_PER_CHAR = 0.008
GB = 1024 ** 3

VOICE_FREQS = (330.0, 440.0, 550.0, 660.0, 770.0, 880.0, 990.0, 1100.0)


def freq_for(name: str) -> float:
    """Stable, distinct-enough pitch for a voice name."""
    return VOICE_FREQS[zlib.crc32(name.encode()) % len(VOICE_FREQS)]


def tone(freq: float, seconds: float, rate: int) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def write_tone_wav(path: Path, freq: float, seconds: float = 3.0, rate: int = 24000) -> None:
    import soundfile as sf
    sf.write(str(path), tone(freq, seconds, rate), rate, subtype="PCM_16")


def write_tone_mp3(path: str, freq: float, seconds: float, rate: int = 24000) -> None:
    from pydub import AudioSegment
    pcm = (tone(freq, seconds, rate) * 32767).astype("<i2")
    AudioSegment(data=pcm.tobytes(), sample_width=2, frame_rate=rate, channels=1).export(path, format="mp3")


def mp3_duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def decode_mp3(path, rate: int = 24000) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def dominant_freqs(path, rate: int = 24000, window_seconds: float = 0.5) -> List[float]:
    """Dominant frequency of every non-silent window; lets tests read back which voice spoke."""
    samples = decode_mp3(path, rate)
    size = int(window_seconds * rate)
    found = []
    for start in range(0, len(samples) - size + 1, size):
        window = samples[start:start + size]
        if np.sqrt(np.mean(window ** 2)) < 0.05:
            continue
        spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window))))
        found.append(float(np.argmax(spectrum) * rate / len(window)))
    return found


def silent_fraction(path, rate: int = 24000, window_seconds: float = 0.25) -> float:
    samples = decode_mp3(path, rate)
    size = int(window_seconds * rate)
    windows = [samples[i:i + size] for i in range(0, len(samples) - size + 1, size)]
    if not windows:
        return 1.0
    return sum(1 for w in windows if np.sqrt(np.mean(w ** 2)) < 0.02) / len(windows)


def sample_text(chars: int = 2400, prefix: str = "Sentence") -> str:
    """Natural-looking text of roughly *chars* characters (several chunks)."""
    parts, n = [], 0
    i = 1
    while n < chars:
        s = f"{prefix} number {i} speaks about a quiet river and a small village."
        parts.append(s)
        n += len(s) + 1
        i += 1
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Edge TTS
# ---------------------------------------------------------------------------

class FakeNetwork:
    def __init__(self):
        self.online = True
        self.wait_calls: List[dict] = []
        self.recover_on_wait = True

    def is_connected(self, timeout: float = 3.0) -> bool:
        return self.online

    def wait_for_network(self, *a, **k) -> bool:
        toggle = k.get("auto_toggle_wlan", a[2] if len(a) > 2 else None)
        self.wait_calls.append({"args": a, "kwargs": dict(k), "auto_toggle_wlan": toggle})
        if self.recover_on_wait:
            self.online = True
        return self.online


class FakeEdgeTts:
    """Stand-in for the ``edge_tts`` module."""

    def __init__(self):
        self.calls: List[dict] = []
        self.failures: List[Exception] = []  # popped one per call
        self.always_fail: Optional[Exception] = None
        self.gate: Optional[threading.Event] = None
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        outer = self

        class Communicate:
            def __init__(self, text, voice, *a, **k):
                self.text, self.voice = text, voice

            async def save(self, path):
                with outer._lock:
                    outer.calls.append({"text": self.text, "voice": self.voice})
                    err = outer.failures.pop(0) if outer.failures else outer.always_fail
                with outer._lock:
                    outer.active += 1
                    outer.max_active = max(outer.max_active, outer.active)
                try:
                    await asyncio.sleep(0.03)
                    if outer.gate is not None:
                        while not outer.gate.is_set():
                            await asyncio.sleep(0.02)
                    if err is not None:
                        raise err
                    write_tone_mp3(path, freq_for(self.voice), max(0.3, len(self.text) * SECONDS_PER_CHAR))
                finally:
                    with outer._lock:
                        outer.active -= 1

        self.Communicate = Communicate

    def voices_used(self) -> set:
        return {c["voice"] for c in self.calls}


# ---------------------------------------------------------------------------
# Piper (Wyoming server + docker)
# ---------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeWyomingServer:
    """A real Wyoming TCP server that answers ``Synthesize`` with a sine tone."""

    RATE = 22050

    def __init__(self, port: int):
        self.port = port
        self.requests: List[dict] = []
        self.behaviours: List[str] = []  # popped per request: "ok" | "truncate" | "drop"
        self.default_behaviour: Optional[str] = None
        self.gate: Optional[threading.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._ready = threading.Event()
        self.start_count = 0
        self.active = 0
        self.max_active = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._ready.clear()
        self.start_count += 1
        self._thread = threading.Thread(target=self._run, daemon=True, name="FakeWyoming")
        self._thread.start()
        assert self._ready.wait(5), "fake Wyoming server did not start"

    def stop(self) -> None:
        if not self.running:
            return
        loop = self._loop

        def _shutdown():
            self._server.close()
            loop.stop()

        loop.call_soon_threadsafe(_shutdown)
        self._thread.join(5)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._server = self._loop.run_until_complete(
            asyncio.start_server(self._handle, "127.0.0.1", self.port)
        )
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self._server.wait_closed())
            self._loop.close()

    async def _handle(self, reader, writer):
        from wyoming.audio import AudioChunk, AudioStart, AudioStop
        from wyoming.event import async_read_event, async_write_event
        from wyoming.tts import Synthesize
        try:
            event = await async_read_event(reader)
            if event is None or not Synthesize.is_type(event.type):
                return
            synth = Synthesize.from_event(event)
            voice = synth.voice.name if synth.voice else ""
            self.requests.append({"text": synth.text, "voice": voice})
            behaviour = self.behaviours.pop(0) if self.behaviours else (self.default_behaviour or "ok")
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            while self.gate is not None and not self.gate.is_set():
                await asyncio.sleep(0.02)
            if behaviour == "drop":
                self.active -= 1
                return
            samples = tone(freq_for(voice), max(0.3, len(synth.text) * SECONDS_PER_CHAR), self.RATE)
            pcm = (samples * 32767).astype("<i2").tobytes()
            await async_write_event(AudioStart(rate=self.RATE, width=2, channels=1).event(), writer)
            half = len(pcm) // 2
            parts = [pcm[:half]] if behaviour == "truncate" else [pcm[:half], pcm[half:]]
            for part in parts:
                await async_write_event(
                    AudioChunk(rate=self.RATE, width=2, channels=1, audio=part).event(), writer
                )
            if behaviour != "truncate":
                await async_write_event(AudioStop().event(), writer)
            self.active -= 1
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


class FakeDocker:
    """DockerManager replacement: ``compose up`` starts the fake Wyoming server."""

    def __init__(self, server: FakeWyomingServer):
        self.server = server
        self.docker_available = True
        self.image_available = True
        self.foreign_listener: Optional[socket.socket] = None
        self.up_calls: List[dict] = []
        self.down_calls = 0
        self.pull_calls = 0
        self.compose_error: Optional[Exception] = None

    def is_docker_available(self) -> bool:
        return self.docker_available

    def is_piper_image_available(self) -> bool:
        return self.image_available

    def is_piper_port_open(self, port: int = 10200) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def is_port_occupied_by_other(self, port: int = 10200) -> bool:
        return self.foreign_listener is not None and self.is_piper_port_open(port)

    def pull_piper_image(self) -> None:
        self.pull_calls += 1
        self.image_available = True

    def start_piper_compose(self, env=None) -> None:
        self.up_calls.append(dict(env or {}))
        if self.compose_error is not None:
            raise self.compose_error
        self.server.start()

    def stop_piper_compose(self) -> None:
        self.down_calls += 1
        self.server.stop()

    def get_container_logs(self, lines: int = 50) -> str:
        return ""

    def check_prerequisites(self):
        return self.docker_available, ""


# ---------------------------------------------------------------------------
# XTTS-v2 (torch + TTS fakes)
# ---------------------------------------------------------------------------

ACTIVE_GPU: Optional["FakeGpu"] = None


class FakeXttsModel:
    """Loaded-model stand-in; weakly tracked so the fake VRAM is freed only when it is released."""

    def __init__(self, gpu: "FakeGpu"):
        self.gpu = gpu
        self.synthesizer = types.SimpleNamespace(tts_model=self)
        self.latent_calls = 0

    def get_conditioning_latents(self, audio_path):
        self.latent_calls += 1
        self.gpu.latent_requests.append(list(audio_path))
        import soundfile as sf
        data, rate = sf.read(audio_path[0])
        spectrum = np.abs(np.fft.rfft(data))
        return float(np.argmax(spectrum) * rate / len(data)), None

    def inference(self, text, language, gpt_cond_latent, speaker_embedding):
        return {"wav": self.gpu.synthesize(text, language, gpt_cond_latent)}

    def tts(self, text, speaker_wav, language):
        freq, _ = self.get_conditioning_latents([speaker_wav])
        return self.gpu.synthesize(text, language, freq)


class FakeGpu:
    """Fake CUDA device + ``torch`` module + ``TTS`` package for the XTTS engine."""

    MODEL_BYTES = int(2.5 * GB)

    def __init__(self, total_gb: float = 8.0):
        self.total = int(total_gb * GB)
        self.cuda = True
        self.models: "weakref.WeakSet[FakeXttsModel]" = weakref.WeakSet()
        self.loads = 0
        self.downloads = 0
        self.empty_cache_calls = 0
        self.inference_calls: List[dict] = []
        self.latent_requests: List[list] = []
        self.oom_on_calls: set = set()
        self.fail_calls: Dict[int, Exception] = {}
        self.gate: Optional[threading.Event] = None
        self.entered = threading.Event()
        self.load_error: Optional[Exception] = None
        self.always_fail: Optional[Exception] = None
        self._lock = threading.Lock()
        self._active = 0
        self.max_concurrent = 0

    # -- torch --------------------------------------------------------
    def allocated(self) -> int:
        gc.collect()
        return len(self.models) * self.MODEL_BYTES

    def torch_module(self) -> types.ModuleType:
        mod = types.ModuleType("torch")
        mod.cuda = types.SimpleNamespace(
            is_available=lambda: self.cuda,
            get_device_name=lambda i=0: "Fake RTX",
            mem_get_info=lambda device=0: (self.total - self.allocated(), self.total),
            memory_allocated=lambda *a: self.allocated(),
            synchronize=lambda *a: None,
            ipc_collect=lambda: None,
            empty_cache=self._empty_cache,
        )
        mod.inference_mode = lambda *a, **k: nullcontext()
        return mod

    def _empty_cache(self) -> None:
        self.empty_cache_calls += 1

    # -- TTS ----------------------------------------------------------
    def new_model(self, model_name=None, progress_bar=False, gpu=False):
        if gpu:
            if self.load_error is not None:
                raise self.load_error
            model = FakeXttsModel(self)
            self.models.add(model)
            self.loads += 1
            return model
        self.downloads += 1
        self.mark_cached()
        return FakeXttsModel(self)

    cache_dir: Optional[Path] = None

    def mark_cached(self) -> None:
        from src.infrastructure import xtts_model_manager as xmm
        d = Path(xmm._TTS_CACHE_DIR) / xmm._XTTS_MODEL_CACHE_SUBDIR
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.pth").write_bytes(b"weights")

    def synthesize(self, text: str, language: str, freq: float) -> np.ndarray:
        with self._lock:
            index = len(self.inference_calls) + 1
            self.inference_calls.append({"text": text, "language": language, "freq": freq})
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(30), "XTTS gate was never opened"
            if index in self.oom_on_calls:
                raise RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
            if index in self.fail_calls:
                raise self.fail_calls[index]
            if self.always_fail is not None:
                raise type(self.always_fail)(*self.always_fail.args)  # fresh instance: no shared traceback
            return tone(freq, max(0.2, len(text) * SECONDS_PER_CHAR), 24000)
        finally:
            with self._lock:
                self._active -= 1

    def install_package(self, site_dir: Path) -> None:
        """Writes a real (fake) ``TTS`` package so ``importlib.util.find_spec('TTS')`` works."""
        pkg = site_dir / "TTS"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "api.py").write_text(
            "from tests.e2e import rig\n"
            "def TTS(*a, **k):\n"
            "    return rig.ACTIVE_GPU.new_model(*a, **k)\n"
        )


# ---------------------------------------------------------------------------
# Dialog recording
# ---------------------------------------------------------------------------

class DialogRecorder:
    def __init__(self):
        self.messages: List[tuple] = []

    def _record(self, kind):
        def fn(parent, title, text, *a, **k):
            self.messages.append((kind, title, text))
        return staticmethod(fn)

    def as_class(self):
        rec = self

        class FakeMessageBox:
            warning = rec._record("warning")
            critical = rec._record("critical")
            information = rec._record("information")
            question = rec._record("question")

        return FakeMessageBox

    def texts(self, kind: Optional[str] = None) -> List[str]:
        return [t for k, _, t in self.messages if kind is None or k == kind]


# ---------------------------------------------------------------------------
# The application rig
# ---------------------------------------------------------------------------

class AppRig:
    """The whole application, driven only through its widgets."""

    def __init__(self, workdir: Path, monkeypatch, *, with_speakers: bool = True,
                 model_cached: bool = True, piper_models: bool = True, total_vram_gb: float = 8.0):
        global ACTIVE_GPU
        from src.application.services.piper_setup_service import PiperSetupService
        from src.domain.piper_audio_generator import PIPER_VOICE_MAPPING, PiperAudioGenerator
        from src.infrastructure import xtts_model_manager as xmm
        from src.infrastructure.ioc import Container
        from src.infrastructure.piper_model_manager import PiperModelManager
        from src.infrastructure.xtts_model_manager import XttsModelManager
        from src.main import ReadAloudApplication
        from src.gui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication(sys.argv)
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.inputs = self.workdir / "inputs"
        self.out = self.workdir / "out"
        self.inputs.mkdir(exist_ok=True)
        self.out.mkdir(exist_ok=True)
        self.state_file = self.workdir / "state.json"
        self._monkeypatch = monkeypatch

        # --- GPU / XTTS ---
        self.gpu = FakeGpu(total_vram_gb)
        ACTIVE_GPU = self.gpu
        site = self.workdir / "site"
        self.gpu.install_package(site)
        monkeypatch.syspath_prepend(str(site))
        for name in [n for n in sys.modules if n == "TTS" or n.startswith("TTS.")]:
            monkeypatch.delitem(sys.modules, name)
        monkeypatch.setitem(sys.modules, "torch", self.gpu.torch_module())
        monkeypatch.setattr(xmm, "_TTS_CACHE_DIR", self.workdir / "tts_cache")
        if model_cached:
            self.gpu.mark_cached()
        self.speakers_dir = self.workdir / "speakers"
        if with_speakers:
            self.write_speakers()

        # --- Piper ---
        self.server = FakeWyomingServer(free_port())
        self.docker = FakeDocker(self.server)
        self.piper_models_dir = self.workdir / "piper_models"
        self.piper_models = PiperModelManager(models_dir=self.piper_models_dir)
        if piper_models:
            for genders in PIPER_VOICE_MAPPING.values():
                for voice in genders.values():
                    for ext in (".onnx", ".onnx.json"):
                        (self.piper_models_dir / f"{voice}{ext}").write_bytes(b"model")

        # --- Edge ---
        self.edge = FakeEdgeTts()
        self.network = FakeNetwork()

        # --- container, wired like main() ---
        self.container = Container(state_file_path=str(self.state_file))
        self.container._network_manager = self.network
        self.container.audio_generator._edge_tts = self.edge
        self.container._docker_manager = self.docker
        self.container._piper_model_manager = self.piper_models
        self.container._piper_setup_service = PiperSetupService(
            docker_manager=self.docker, model_manager=self.piper_models, piper_port=self.server.port
        )
        self.container._piper_audio_generator = PiperAudioGenerator(port=self.server.port)
        self.xtts_manager = XttsModelManager(speakers_dir=self.speakers_dir)
        self.container._xtts_model_manager = self.xtts_manager

        self.dialogs = DialogRecorder()
        box = self.dialogs.as_class()
        monkeypatch.setattr("src.main.QMessageBox", box)
        monkeypatch.setattr("src.gui.widgets.tts_backend_selector.QMessageBox", box)

        self.controller = self.container.app_controller
        self.window = MainWindow(
            piper_setup_service=self.container.piper_setup_service,
            xtts_setup_service=self.container.xtts_setup_service,
        )
        self.coordinator = ReadAloudApplication(self.window, self.controller)
        self.controller.restore_state()
        self.window.show()
        self.pump()
        self._closed = False

    # -- fixtures of the environment -------------------------------------
    def write_speakers(self) -> None:
        """Reference WAVs: each (language, gender) pair has its own pitch -> its own cloned voice."""
        for lang in ("uk", "en", "de", "ru"):
            for gender in ("male", "female"):
                d = self.speakers_dir / lang
                d.mkdir(parents=True, exist_ok=True)
                write_tone_wav(d / f"{gender}.wav", self.speaker_freq(lang, gender))

    @staticmethod
    def speaker_freq(lang: str, gender: str) -> float:
        table = {"uk": 300.0, "en": 500.0, "de": 700.0, "ru": 900.0}
        return table[lang] + (100.0 if gender == "female" else 0.0)

    def write_text(self, name: str = "book.txt", text: Optional[str] = None, directory: Optional[Path] = None) -> Path:
        path = (directory or self.inputs) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sample_text() if text is None else text, encoding="utf-8")
        return path

    def fast_retries(self) -> None:
        """Removes back-off delays so failure scenarios finish quickly."""
        import functools
        from src.application import app_controller as ac
        from src.domain import retry_budget
        self._monkeypatch.setattr(ac, "RETRY_SWEEP_DELAY_MS", 50, raising=False)
        self._monkeypatch.setattr(retry_budget, "MIN_RETRY_BUDGET", 4)
        self._monkeypatch.setattr("random.uniform", lambda a, b: 0.0)
        for gen in (self.container.audio_generator, self.container.piper_audio_generator,
                    self.container.xtts_audio_generator):
            original = gen.generate_audio_batch
            self._monkeypatch.setattr(
                gen, "generate_audio_batch",
                functools.partial(_with_backoff, original), raising=False,
            )

    # -- engine access (one uniform view over the three fakes) -------------------
    def engine_calls(self, backend: TtsBackend) -> List[str]:
        if backend == TtsBackend.EDGE_TTS:
            return [c["text"] for c in self.edge.calls]
        if backend == TtsBackend.PIPER:
            return [r["text"] for r in self.server.requests]
        return [c["text"] for c in self.gpu.inference_calls]

    def engine_gate(self, backend: TtsBackend) -> threading.Event:
        """Blocks the engine inside its first synthesis call until the returned event is set."""
        gate = threading.Event()
        if backend == TtsBackend.EDGE_TTS:
            self.edge.gate = gate
        elif backend == TtsBackend.PIPER:
            self.server.gate = gate
        else:
            self.gpu.gate = gate
        return gate

    def break_engine(self, backend: TtsBackend) -> None:
        if backend == TtsBackend.EDGE_TTS:
            self.edge.always_fail = ConnectionResetError("network is unreachable")
        elif backend == TtsBackend.PIPER:
            self.server.default_behaviour = "drop"
        else:
            self.gpu.always_fail = RuntimeError("phonemizer crashed")

    def heal_engine(self, backend: TtsBackend) -> None:
        self.edge.always_fail = None
        self.server.default_behaviour = None
        self.gpu.always_fail = None

    # -- event loop ------------------------------------------------------
    def pump(self, seconds: float = 0.0) -> None:
        end = time.monotonic() + seconds
        self.app.processEvents()
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 40.0, what: str = "condition") -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError(f"timed out after {timeout}s waiting for {what}\n{self.describe()}")

    def describe(self) -> str:
        tasks = [(t.config.project_name, t.status.value, t.message) for t in self.container.queue_service.get_all_tasks()]
        return f"tasks={tasks} dialogs={self.dialogs.messages}"

    # -- state queries -----------------------------------------------------
    def tasks(self):
        return self.container.queue_service.get_all_tasks()

    def task(self, name: Optional[str] = None):
        tasks = self.tasks()
        if name is None:
            assert len(tasks) == 1, self.describe()
            return tasks[0]
        return next(t for t in tasks if t.config.project_name == name)

    def is_idle(self) -> bool:
        return not any(t.status in (TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.PAUSED)
                       for t in self.tasks())

    def wait_idle(self, timeout: float = 60.0) -> None:
        self.wait_for(self.is_idle, timeout, "all tasks to finish")
        self.pump(0.05)

    def final_files(self) -> List[Path]:
        return sorted((self.out / "final").glob("*.mp3")) if (self.out / "final").exists() else []

    def leftover_work_dirs(self) -> List[Path]:
        """Temporary text_/audio_ directories left behind anywhere the app could have put them."""
        found = []
        for base in (self.inputs, self.out, self.workdir):
            for p in base.glob("*"):
                if p.is_dir() and p.name.startswith(("text_", "audio_")):
                    found.append(p)
        return found

    def queue_widget(self, task):
        return self.window.queue_list._items.get(str(task.id))

    # -- driving the UI ------------------------------------------------------
    def choose_files(self, *paths: Path) -> None:
        self._monkeypatch.setattr(
            "src.gui.widgets.file_selector.QFileDialog.getOpenFileNames",
            staticmethod(lambda *a, **k: ([str(p) for p in paths], "")),
        )
        self.window.file_selector._select_button.click()
        self.pump()

    def choose_output(self, directory: Optional[Path] = None) -> None:
        d = directory or self.out
        self._monkeypatch.setattr(
            "src.gui.widgets.output_selector.QFileDialog.getExistingDirectory",
            staticmethod(lambda *a, **k: str(d)),
        )
        self.window.output_selector._select_button.click()
        self.pump()

    def select_backend(self, backend: TtsBackend) -> None:
        sel = self.window.tts_backend_selector
        radio = {TtsBackend.EDGE_TTS: sel._edge_radio, TtsBackend.PIPER: sel._piper_radio,
                 TtsBackend.XTTS: sel._xtts_radio}[backend]
        radio.click()
        self.pump()

    def set_language(self, display: str) -> None:
        self.window.language_selector._combo.setCurrentText(display)
        self.pump()

    def set_gender(self, display: str) -> None:
        self.window.gender_selector._combo.setCurrentText(display)
        self.pump()

    def set_speed(self, text: str) -> None:
        self.window.speed_selector._combo.setCurrentText(text)

    def set_threads(self, n: int) -> None:
        self.window.thread_selector._spinbox.setValue(n)

    def set_project_name(self, name: str) -> None:
        self.window.project_input._input.setText(name)
        self.window.project_input._input.textEdited.emit(name)

    def click_add(self) -> None:
        self.window.control_buttons._start_button.click()
        self.pump()

    def submit(self, path: Path, backend: TtsBackend = TtsBackend.EDGE_TTS, language: str = "English",
               gender: str = "Male", speed: str = "1.0", name: Optional[str] = None) -> None:
        """Everything a user does to queue one file."""
        self.choose_files(path)
        self.choose_output()
        self.select_backend(backend)
        self.set_language(language)
        self.set_gender(gender)
        self.set_speed(speed)
        if name:
            self.set_project_name(name)
        self.click_add()

    def wait_backend_panel(self, backend: TtsBackend, timeout: float = 15.0) -> None:
        """Waits for the background prerequisite check that a backend switch triggers."""
        sel = self.window.tts_backend_selector
        if backend == TtsBackend.XTTS:
            self.wait_for(lambda: sel._last_xtts_result is not None, timeout, "XTTS diagnostics")
        elif backend == TtsBackend.PIPER:
            self.wait_for(lambda: sel._last_piper_result is not None, timeout, "Piper diagnostics")

    def queue_button(self, task, which: str):
        w = self.queue_widget(task)
        return {"pause": w.pause_button, "delete": w.delete_button}[which]

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.controller.shutdown()
        finally:
            sel = self.window.tts_backend_selector
            for attr in ("_piper_check_thread", "_xtts_check_thread", "_xtts_action_thread"):
                thread = getattr(sel, attr, None)
                if thread is not None:
                    thread.quit()
                    thread.wait(3000)
            self.server.stop()
            self.window.close()
            self.pump()


def _with_backoff(original, *args, **kwargs):
    import inspect
    kwargs.setdefault("backoff", 0.01)
    if "max_retries" in inspect.signature(original).parameters:
        kwargs["max_retries"] = min(kwargs.get("max_retries", 3), 3)  # stay below the 30 s floor that starts at attempt 5
    return original(*args, **kwargs)
