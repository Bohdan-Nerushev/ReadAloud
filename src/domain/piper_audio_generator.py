"""
Piper TTS audio generator.

Synthesises audio via the Wyoming protocol (TCP) using a locally running
rhasspy/wyoming-piper Docker container. Output is WAV, which is converted
to MP3 via pydub so the rest of the pipeline remains format-agnostic.

Design decisions:
  - One synthesis request at a time (Piper is single-threaded per container).
    max_workers is intentionally capped at 1 inside the async batch runner.
  - WAV → MP3 conversion is done inside the worker coroutine, before the
    result is returned, so callers always receive an .mp3 path.
  - Retry logic mirrors AudioGenerator: transient errors back-off and retry;
    fatal errors abort immediately.
  - The background asyncio loop lifecycle matches AudioGenerator exactly,
    so GenerationService can drive both implementations identically.
"""

import asyncio
import io
import logging
import os
import random
import threading
import wave
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from wyoming.audio import AudioChunk as WyomingAudioChunk
from wyoming.audio import AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice

from src.domain.exceptions import (
    FatalGenerationException,
    PiperConnectionException,
    TransientGenerationException,
)
from src.domain.models import AudioChunk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Voice mapping: (language, gender) -> wyoming voice name
# Extend this dict to add more languages/voices.
# ---------------------------------------------------------------------------
PIPER_VOICE_MAPPING: dict[str, dict[str, str]] = {
    "ru": {
        "male": "ru_RU-dmitri-medium",
        "female": "ru_RU-irina-medium",
    },
    "en": {
        "male": "en_US-ryan-medium",
        "female": "en_US-amy-medium",
    },
    "de": {
        "male": "de_DE-thorsten-medium",
        "female": "de_DE-eva_k-x_low",
    },
    "uk": {
        # Piper only has one Ukrainian model; used for both genders.
        "male": "uk_UA-lada-x_low",
        "female": "uk_UA-lada-x_low",
    },
    "fr": {
        "male": "fr_FR-upmc-medium",
        "female": "fr_FR-siwis-medium",
    },
    "es": {
        "male": "es_ES-davefx-medium",
        "female": "es_ES-sharvard-medium",
    },
    "it": {
        "male": "it_IT-riccardo-x_low",
        "female": "it_IT-riccardo-x_low",  # Only one Italian model available.
    },
}

# Languages that Piper currently supports (subset of EdgeTTS languages)
PIPER_SUPPORTED_LANGUAGES = frozenset(PIPER_VOICE_MAPPING.keys())

# ---------------------------------------------------------------------------
# Error classification helpers (mirrors AudioGenerator)
# ---------------------------------------------------------------------------

_PIPER_FATAL_EXCEPTIONS = (
    ValueError, TypeError, AttributeError, KeyError, IndexError,
    FileNotFoundError, PermissionError, FatalGenerationException,
)

_PIPER_TRANSIENT_EXCEPTIONS = (
    PiperConnectionException,
    TransientGenerationException,
    asyncio.TimeoutError,
    TimeoutError,
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    OSError,
    EOFError,
)


def _is_piper_transient_error(exc: BaseException) -> bool:
    """Returns True if the error is likely transient and a retry may succeed."""
    if isinstance(exc, _PIPER_FATAL_EXCEPTIONS):
        return False
    if isinstance(exc, _PIPER_TRANSIENT_EXCEPTIONS):
        return True

    msg = str(exc).lower()
    transient_keywords = (
        "connection refused", "timeout", "timed out", "eof",
        "connection reset", "broken pipe", "network", "closed",
    )
    return any(kw in msg for kw in transient_keywords)


class PiperAudioGenerator:
    """
    TTS generator that synthesises audio via a locally running wyoming-piper container.

    Public interface is intentionally identical to AudioGenerator so that
    GenerationService can substitute either backend transparently.

    Thread-safety:
        ``generate_audio_batch`` may be called from multiple threads; it schedules
        coroutines on a single dedicated asyncio loop running in a daemon thread.

    Concurrency note:
        Piper processes one synthesis request at a time. The internal semaphore
        is set to max_concurrency=1 to prevent overwhelming the container.
        Multiple batches submitted from GenerationService are queued and processed
        sequentially inside the loop.
    """

    # Sentinel used to stop async workers cleanly
    _STOP_SENTINEL = object()

    def __init__(
            self,
            host: str = "127.0.0.1",
            port: int = 10200,
            connection_timeout: float = 10.0,
            synthesis_timeout: float = 120.0,
    ) -> None:
        """
        Initialises the generator.

        Args:
            host:               Wyoming server host.
            port:               Wyoming server TCP port.
            connection_timeout: Seconds to wait when establishing TCP connection.
            synthesis_timeout:  Seconds to wait for the full synthesis to complete.
        """
        self._host = host
        self._port = port
        self._connection_timeout = connection_timeout
        self._synthesis_timeout = synthesis_timeout

        self._init_lock = threading.Lock()
        self._loop_ready = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        # Piper is single-threaded per container: max_concurrency=1
        self._semaphore: Optional[asyncio.Semaphore] = None

        self.ensure_loop_running()

    # ------------------------------------------------------------------
    # Loop lifecycle (mirrors AudioGenerator)
    # ------------------------------------------------------------------

    def ensure_loop_running(self) -> None:
        """Ensures the background asyncio event loop thread is active."""
        with self._init_lock:
            if (
                self._loop is None
                or not self._loop.is_running()
                or self._loop_thread is None
                or not self._loop_thread.is_alive()
            ):
                self._loop_ready.clear()
                self._loop = asyncio.new_event_loop()
                self._loop_thread = threading.Thread(
                    target=self._start_background_loop,
                    args=(self._loop,),
                    daemon=True,
                    name="PiperAudioGenerator-AsyncLoop",
                )
                self._loop_thread.start()
                if not self._loop_ready.wait(timeout=10.0):
                    raise RuntimeError(
                        "PiperAudioGenerator: background asyncio loop did not start within 10 seconds"
                    )

    def _start_background_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        # max_concurrency=1: one synthesis at a time (Piper is single-threaded)
        self._semaphore = asyncio.Semaphore(1)
        self._loop_ready.set()
        loop.run_forever()

    # ------------------------------------------------------------------
    # Public interface (implements TtsGeneratorProtocol)
    # ------------------------------------------------------------------

    def generate_audio(
            self,
            chunk: AudioChunk,
            language: str,
            gender: str,
            output_dir: str,
    ) -> Tuple[str, float]:
        """Generates audio for a single chunk. Delegates to generate_audio_batch."""
        results = self.generate_audio_batch([chunk], language, gender, output_dir)
        return results[0]

    def generate_audio_batch(
            self,
            chunks: List[AudioChunk],
            language: str,
            gender: str,
            output_dir: str,
            chunk_callback: Optional[Callable[[int, str, float], None]] = None,
            max_workers: int = 10,
            max_retries: int = 10,
            backoff: float = 2.0,
    ) -> List[Tuple[str, float]]:
        """
        Generates MP3 audio files for a list of chunks using the Piper container.

        Args:
            chunks:         Chunks to synthesise.
            language:       ISO language code (must be in PIPER_SUPPORTED_LANGUAGES).
            gender:         "male" or "female".
            output_dir:     Directory where .mp3 files will be written.
            chunk_callback: Optional callable(chunk_number, file_path, duration).
            max_workers:    Ignored for Piper (always 1); kept for interface parity.
            max_retries:    Maximum retry attempts per chunk.
            backoff:        Base back-off delay in seconds.

        Returns:
            List of (file_path, duration) tuples aligned to *chunks*.
            Entries for failed chunks contain ``None``.

        Raises:
            ValueError: If ``language`` is not in PIPER_SUPPORTED_LANGUAGES.
        """
        if not chunks:
            return []

        if language not in PIPER_SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Language '{language}' is not supported by Piper. "
                f"Supported: {sorted(PIPER_SUPPORTED_LANGUAGES)}"
            )

        self.ensure_loop_running()

        voice = PIPER_VOICE_MAPPING[language][gender]
        output_path = Path(output_dir)

        async def _run_batch() -> List[Optional[Tuple[str, float]]]:
            return await self._generate_batch_async(
                chunks=chunks,
                voice=voice,
                output_path=output_path,
                max_retries=max_retries,
                backoff=backoff,
                chunk_callback=chunk_callback,
            )

        try:
            future = asyncio.run_coroutine_threadsafe(_run_batch(), self._loop)
            return future.result(timeout=None)
        except TimeoutError as e:
            future.cancel()
            msg = "Piper batch generation timed out"
            logger.error(msg, exc_info=True)
            raise TimeoutError(msg) from e
        except Exception as e:
            future.cancel()
            logger.error("Piper batch generation failed: %s", e, exc_info=True)
            raise Exception(f"Piper batch generation failed: {str(e) or type(e).__name__}") from e

    def close(self) -> None:
        """Stops the background asyncio event loop."""
        if hasattr(self, "_loop") and self._loop:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if hasattr(self, "_loop_thread") and self._loop_thread and self._loop_thread.is_alive():
                self._loop_thread.join(timeout=2.0)
            try:
                self._loop.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal async batch runner
    # ------------------------------------------------------------------

    async def _generate_batch_async(
            self,
            chunks: List[AudioChunk],
            voice: str,
            output_path: Path,
            max_retries: int,
            backoff: float,
            chunk_callback: Optional[Callable[[int, str, float], None]],
    ) -> List[Optional[Tuple[str, float]]]:
        """
        Processes chunks sequentially (Piper semaphore=1) with retry per chunk.
        Mirrors the producer/consumer pattern of AudioGenerator._generate_batch_async.
        """
        queue: asyncio.Queue = asyncio.Queue()
        for idx, chunk in enumerate(chunks):
            await queue.put((idx, chunk))
        await queue.put(self._STOP_SENTINEL)

        results: List[Optional[Tuple[str, float]]] = [None] * len(chunks)

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    if item is self._STOP_SENTINEL:
                        return

                    idx, chunk = item
                    try:
                        res = await self._generate_one_with_retry(
                            chunk=chunk,
                            voice=voice,
                            output_path=output_path,
                            max_retries=max_retries,
                            backoff=backoff,
                        )
                        results[idx] = res
                        if chunk_callback:
                            try:
                                chunk_callback(chunk.chunk_number, res[0], res[1])
                            except Exception as cb_err:
                                logger.error(
                                    "Error in chunk callback for chunk %d: %s",
                                    chunk.chunk_number, cb_err, exc_info=True,
                                )
                    except Exception as exc:
                        logger.error(
                            "Chunk %d failed permanently after %d attempt(s): %s",
                            chunk.chunk_number, max_retries, exc, exc_info=True,
                        )
                        results[idx] = None
                finally:
                    queue.task_done()

        # Single worker (Piper is single-threaded per container)
        worker_task = asyncio.create_task(worker())
        try:
            await asyncio.gather(worker_task)
        except asyncio.CancelledError:
            if not worker_task.done():
                worker_task.cancel()
            raise

        return results

    # ------------------------------------------------------------------
    # Single-chunk synthesis with retry
    # ------------------------------------------------------------------

    async def _generate_one_with_retry(
            self,
            chunk: AudioChunk,
            voice: str,
            output_path: Path,
            max_retries: int = 10,
            backoff: float = 2.0,
    ) -> Tuple[str, float]:
        """Synthesises one chunk with exponential back-off retry."""
        if not chunk.text_content or not chunk.text_content.strip():
            raise FatalGenerationException(
                f"Chunk {chunk.chunk_number} text content is empty"
            )

        last_exception: Optional[BaseException] = None

        for attempt in range(max_retries):
            try:
                output_path.mkdir(parents=True, exist_ok=True)
                mp3_path = output_path / f"{chunk.chunk_number}.mp3"
                wav_tmp_path = output_path / f"{chunk.chunk_number}.wav.tmp"
                mp3_tmp_path = output_path / f"{chunk.chunk_number}.mp3.tmp"

                async with self._semaphore:
                    await asyncio.wait_for(
                        self._synthesise_to_wav(chunk.text_content, voice, wav_tmp_path),
                        timeout=self._synthesis_timeout,
                    )

                # WAV → MP3 conversion in a thread pool to avoid blocking the loop
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    self._convert_wav_to_mp3,
                    wav_tmp_path,
                    mp3_tmp_path,
                )

                # Atomic swap
                os.replace(str(mp3_tmp_path), str(mp3_path))

                duration = await loop.run_in_executor(
                    None, self._get_mp3_duration, str(mp3_path)
                )

                logger.debug(
                    "Chunk %d generated via Piper (attempt %d/%d, duration=%.2fs)",
                    chunk.chunk_number, attempt + 1, max_retries, duration,
                )
                return str(mp3_path.absolute()), duration

            except asyncio.TimeoutError as te:
                last_exception = PiperConnectionException(
                    f"Timeout synthesising chunk {chunk.chunk_number} via Piper",
                    cause=te,
                )
                self._cleanup_tmp_files(wav_tmp_path, mp3_tmp_path)

            except ConnectionRefusedError as ce:
                last_exception = PiperConnectionException(
                    f"Piper container refused connection on port {self._port} "
                    f"(chunk {chunk.chunk_number})",
                    cause=ce,
                )

            except Exception as exc:
                last_exception = exc
                if not _is_piper_transient_error(exc):
                    logger.error(
                        "Fatal error on Piper chunk %d (attempt %d/%d), no retry: %s",
                        chunk.chunk_number, attempt + 1, max_retries, exc,
                    )
                    raise FatalGenerationException(
                        f"Fatal error on Piper chunk {chunk.chunk_number}: {exc}",
                        cause=exc if isinstance(exc, Exception) else None,
                    ) from exc

            remaining = max_retries - attempt - 1
            if remaining > 0:
                delay = min(60.0, backoff * (2 ** attempt) + random.uniform(0, backoff))
                logger.warning(
                    "Transient Piper error on chunk %d (attempt %d/%d): %s. "
                    "Retrying in %.1fs (%d attempt(s) left).",
                    chunk.chunk_number, attempt + 1, max_retries,
                    last_exception, delay, remaining,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "Chunk %d exhausted all %d Piper retry attempts. Last error: %s",
                    chunk.chunk_number, max_retries, last_exception,
                )

        raise last_exception  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Wyoming synthesis coroutine
    # ------------------------------------------------------------------

    async def _synthesise_to_wav(
            self,
            text: str,
            voice: str,
            wav_path: Path,
    ) -> None:
        """
        Connects to the Wyoming server and streams synthesised audio into a WAV file.

        Raises:
            PiperConnectionException: If the server sends no audio data.
            FatalGenerationException: If AudioStop is received before AudioStart.
        """
        voice_obj = SynthesizeVoice(name=voice)
        wav_file: Optional[wave.Wave_write] = None

        try:
            async with AsyncTcpClient(self._host, self._port) as client:
                await client.write_event(Synthesize(text=text, voice=voice_obj).event())

                while True:
                    event = await client.read_event()
                    if event is None:
                        logger.warning("Wyoming connection closed unexpectedly.")
                        break

                    if AudioStart.is_type(event.type):
                        start = AudioStart.from_event(event)
                        wav_file = wave.open(str(wav_path), "wb")
                        wav_file.setnchannels(start.channels)
                        wav_file.setsampwidth(start.width)
                        wav_file.setframerate(start.rate)
                        logger.debug(
                            "Piper audio stream started (%d Hz, %d ch, %d-bit).",
                            start.rate, start.channels, start.width * 8,
                        )

                    elif WyomingAudioChunk.is_type(event.type):
                        audio_chunk = WyomingAudioChunk.from_event(event)
                        if wav_file is not None:
                            wav_file.writeframes(audio_chunk.audio)

                    elif AudioStop.is_type(event.type):
                        logger.debug("Piper audio synthesis finished.")
                        break
        finally:
            if wav_file is not None:
                wav_file.close()

        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise PiperConnectionException(
                f"Piper returned no audio data for voice '{voice}'."
            )

    # ------------------------------------------------------------------
    # WAV → MP3 conversion (runs in executor, not async)
    # ------------------------------------------------------------------

    def _convert_wav_to_mp3(self, wav_path: Path, mp3_tmp_path: Path) -> None:
        """
        Converts a WAV file to MP3 using pydub (which calls ffmpeg internally).

        pydub is already in requirements.txt and wraps ffmpeg, so this approach
        avoids adding a subprocess management layer while re-using existing deps.
        """
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(str(wav_path))
            audio.export(str(mp3_tmp_path), format="mp3", bitrate="128k")
        finally:
            # Always remove the temporary WAV regardless of conversion outcome
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Duration extraction
    # ------------------------------------------------------------------

    def _get_mp3_duration(self, file_path: str) -> float:
        """Returns MP3 duration in seconds using mutagen."""
        try:
            from mutagen.mp3 import MP3
            audio = MP3(file_path)
            return float(audio.info.length)
        except Exception:
            return 0.0

    def _get_file_duration_fast(self, file_path: str) -> float:
        """Alias for _get_mp3_duration — provides a uniform interface across all TTS generators."""
        return self._get_mp3_duration(file_path)

    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cleanup_tmp_files(*paths: Optional[Path]) -> None:
        """Silently removes temporary files to avoid leaving stale artifacts."""
        for path in paths:
            if path is not None:
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
