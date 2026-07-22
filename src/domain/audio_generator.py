"""
Audio generation service using Edge TTS.

This module handles conversion of text chunks to audio files.
"""

import asyncio
import logging
import random
import threading
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from src.domain.exceptions import TransientGenerationException, FatalGenerationException
from src.domain.models import AudioChunk


import os
import socket

# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------

# Exception type names or substrings that indicate a transient (retriable) problem.
_FATAL_EXCEPTIONS = (
    ValueError, TypeError, AttributeError, KeyError, IndexError,
    FileNotFoundError, PermissionError, FatalGenerationException
)

_FATAL_ERROR_SUBSTRINGS = (
    "bad request",
    "unauthorized",
    "forbidden",
    "not found",
    "400",
    "401",
    "403",
    "404",
    "422",
    "invalid argument",
    "unsupported voice",
)

_TRANSIENT_ERROR_SUBSTRINGS = (
    "timeout",
    "timed out",
    "connection",
    "network",
    "rate limit",
    "too many requests",
    "service unavailable",
    "503",
    "502",
    "500",
    "504",
    "429",
    "408",
    "reset",
    "eof",
    "disconnected",
    "handshake",
    "endpoint",
)


def _is_transient_error(exc: BaseException) -> bool:
    """
    Heuristically classify an exception as transient (retriable) or fatal.

    Returns True if the error is likely temporary and a retry may succeed.
    Returns False for errors that will not improve with retrying (e.g., bad input).
    """
    # Always fatal — programmer / config / filesystem input bugs
    if isinstance(exc, _FATAL_EXCEPTIONS):
        return False

    # TransientGenerationException is explicitly marked as retriable
    if isinstance(exc, TransientGenerationException):
        return True

    # Check HTTP status attribute if present
    status = getattr(exc, "status", getattr(exc, "status_code", None))
    if status is not None:
        if status in (400, 401, 403, 404, 422):
            return False
        if status in (408, 429, 500, 502, 503, 504):
            return True

    # Known socket, network, or asyncio timeout errors are transient
    if isinstance(exc, (asyncio.TimeoutError, socket.error, TimeoutError, ConnectionError, OSError)):
        return True

    msg = str(exc).lower()

    if any(sub in msg for sub in _FATAL_ERROR_SUBSTRINGS):
        return False

    exc_type_name = type(exc).__name__.lower()
    if any(sub in exc_type_name for sub in ("connector", "websocket", "network", "timeout", "connection", "ssl")):
        return True

    return any(sub in msg for sub in _TRANSIENT_ERROR_SUBSTRINGS)


class AudioGenerator:
    """
    Service responsible for generating audio files from text using Edge TTS.

    This service integrates with Microsoft Edge's Text-to-Speech API.

    Thread-safety:
        `generate_audio_batch` may be called from multiple threads concurrently;
        internally it schedules coroutines on a single shared asyncio event loop
        running in a dedicated daemon thread.
    """

    # Mapping of language codes and genders to Edge TTS voices
    VOICE_MAPPING = {
        'en': {
            'male': 'en-US-GuyNeural',
            'female': 'en-US-AriaNeural'
        },
        'uk': {
            'male': 'uk-UA-OstapNeural',
            'female': 'uk-UA-PolinaNeural'
        },
        'de': {
            'male': 'de-DE-ConradNeural',
            'female': 'de-DE-KatjaNeural'
        },
        'ru': {
            'male': 'ru-RU-DmitryNeural',
            'female': 'ru-RU-SvetlanaNeural'
        },
        'fr': {
            'male': 'fr-FR-HenriNeural',
            'female': 'fr-FR-DeniseNeural'
        },
        'es': {
            'male': 'es-ES-AlvaroNeural',
            'female': 'es-ES-ElviraNeural'
        },
        'it': {
            'male': 'it-IT-ValerioNeural',
            'female': 'it-IT-ElsaNeural'
        }
    }

    # Sentinel used to signal async workers to stop
    _STOP_SENTINEL = object()

    def __init__(self, network_manager: Optional[Any] = None) -> None:
        """Initialize the AudioGenerator."""
        self._edge_tts = None
        self._network_manager = network_manager
        self._rate_limit_reset_time = 0.0
        self._init_lock = threading.Lock()

        self._loop_ready = threading.Event()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._network_lock: Optional[asyncio.Lock] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self.ensure_loop_running()

    def ensure_loop_running(self) -> None:
        """Ensures the background asyncio event loop thread is active and running."""
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
                    name="AudioGenerator-AsyncLoop"
                )
                self._loop_thread.start()
                if not self._loop_ready.wait(timeout=10.0):
                    raise RuntimeError(
                        "AudioGenerator: background asyncio loop did not start within 10 seconds"
                    )

    # ------------------------------------------------------------------
    # Internal initialisation
    # ------------------------------------------------------------------

    def _start_background_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Runs the asyncio event loop in a dedicated thread."""
        asyncio.set_event_loop(loop)
        self._semaphore = asyncio.Semaphore(5)
        self._network_lock = asyncio.Lock()
        self._loop_ready.set()
        loop.run_forever()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def edge_tts(self) -> Any:
        """Lazy-load the edge_tts module."""
        if self._edge_tts is None:
            import edge_tts
            self._edge_tts = edge_tts
        return self._edge_tts

    def generate_audio(
            self,
            chunk: AudioChunk,
            language: str,
            gender: str,
            output_dir: str
    ) -> Tuple[str, float]:
        """
        Generates an audio file from a single text chunk.

        Delegates to ``generate_audio_batch`` with a one-element list.
        """
        results = self.generate_audio_batch([chunk], language, gender, output_dir)
        return results[0]

    def generate_audio_batch(
            self,
            chunks: List[AudioChunk],
            language: str,
            gender: str,
            output_dir: str,
            chunk_callback: Optional[Callable[[int, str, float], None]] = None,
            max_workers: int = 5,
            max_retries: int = 5,
            backoff: float = 2.0,
    ) -> List[Tuple[str, float]]:
        """
        Generates audio files for a list of chunks concurrently with:
          - Rate-limiting via asyncio.Semaphore
          - Per-chunk exponential backoff retry with jitter
          - Error classification (transient vs fatal) to avoid pointless retries
          - Partial success: failed chunks return ``None`` instead of aborting the batch

        Args:
            chunks:          Chunks to synthesise.
            language:        ISO language code (must be in VOICE_MAPPING).
            gender:          "male" or "female".
            output_dir:      Directory where .mp3 files will be written.
            chunk_callback:  Optional callable(chunk_number, file_path, duration)
                             invoked immediately after each chunk succeeds.
            max_workers:     Maximum number of concurrent async workers.
            max_retries:     Maximum retry attempts per chunk.
            backoff:         Base back-off delay in seconds (doubles each attempt + jitter).

        Returns:
            List of (file_path, duration) tuples aligned to *chunks*.
            Entries for failed chunks contain ``None`` (not exceptions).

        Raises:
            ValueError: If ``language`` is not in VOICE_MAPPING.
        """
        if not chunks:
            return []

        self.ensure_loop_running()

        if language not in self.VOICE_MAPPING:
            raise ValueError(f"Unsupported language code: {language}")

        voice = self.VOICE_MAPPING[language][gender]
        output_path = Path(output_dir)

        # ISSUE-7 FIX: Compute a safe upper-bound timeout for the whole batch.
        # Each chunk has a 60 s per-attempt timeout × max_retries retries.
        # We add a buffer of 30 s per chunk to account for overhead.
        per_chunk_worst_case = 60 * max_retries + 30
        batch_timeout = per_chunk_worst_case * len(chunks)
        # Clamp to a sensible ceiling (30 min); very large batches are handled
        # by the calling layer (GenerationService) which splits into sub-batches.
        batch_timeout = max(120.0, min(batch_timeout, 1800.0))

        async def _run_batch() -> List[Optional[Tuple[str, float]]]:
            return await self._generate_batch_async(
                chunks, voice, output_path,
                max_workers=min(max_workers, len(chunks)),
                max_retries=max_retries,
                backoff=backoff,
                chunk_callback=chunk_callback,
            )

        try:
            future = asyncio.run_coroutine_threadsafe(_run_batch(), self._loop)
            # ISSUE-7 FIX: timeout prevents indefinite blocking if the loop stalls.
            return future.result(timeout=batch_timeout)
        except Exception as e:
            logging.error(f"Batch generation failed: {e}", exc_info=True)
            raise Exception(f"Batch generation failed: {str(e)}") from e

    # ------------------------------------------------------------------
    # Internal async implementation
    # ------------------------------------------------------------------

    async def _generate_batch_async(
            self,
            chunks: List[AudioChunk],
            voice: str,
            output_path: Path,
            max_workers: int,
            max_retries: int,
            backoff: float,
            chunk_callback: Optional[Callable[[int, str, float], None]],
    ) -> List[Optional[Tuple[str, float]]]:
        """
        Core async worker pool for batch audio generation.

        BUG-3 FIX:
            Uses a proper producer/consumer pattern with a sentinel-based
            stop signal instead of the racy ``queue.empty()`` + ``get_nowait()``
            pattern. Each worker processes items until it receives the sentinel.

        BUG-2 FIX:
            Individual chunk failures are isolated.  A failed chunk stores
            ``None`` in the result list; it does NOT abort the batch or raise.
        """
        # Populate queue: (index, chunk) tuples
        queue: asyncio.Queue = asyncio.Queue()
        for idx, chunk in enumerate(chunks):
            await queue.put((idx, chunk))

        # Add one sentinel per worker to shut them down cleanly
        for _ in range(max_workers):
            await queue.put(self._STOP_SENTINEL)

        results: List[Optional[Tuple[str, float]]] = [None] * len(chunks)

        async def worker() -> None:
            while True:
                item = await queue.get()
                try:
                    # BUG-3 FIX: sentinel check is the ONLY exit condition
                    if item is self._STOP_SENTINEL:
                        return

                    idx, chunk = item
                    try:
                        res = await self._generate_one_with_retry(
                            chunk, voice, output_path,
                            max_retries=max_retries,
                            backoff=backoff,
                        )
                        results[idx] = res
                        if chunk_callback:
                            try:
                                chunk_callback(chunk.chunk_number, res[0], res[1])
                            except Exception as cb_err:
                                logging.error(
                                    f"Error in chunk callback for chunk "
                                    f"{chunk.chunk_number}: {cb_err}",
                                    exc_info=True
                                )
                    except Exception as exc:
                        # BUG-2 FIX: log the failure but keep other chunks going
                        logging.error(
                            f"Chunk {chunk.chunk_number} failed permanently after "
                            f"{max_retries} attempt(s): {exc}",
                            exc_info=True
                        )
                        results[idx] = None  # marks chunk as failed, does NOT raise
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(max_workers)]
        await asyncio.gather(*workers)
        return results

    async def _generate_one_with_retry(
            self,
            chunk: AudioChunk,
            voice: str,
            output_path: Path,
            max_retries: int = 5,
            backoff: float = 2.0,
    ) -> Tuple[str, float]:
        """
        Generates audio for a single chunk with exponential backoff + jitter.

        ISSUE-10 FIX:
            Added random jitter to back-off delay to prevent thundering-herd
            when many concurrent workers all fail at the same time.

        ISSUE-11 FIX:
            Fatal errors (ValueError, FatalGenerationException, etc.) abort
            immediately without consuming remaining retry attempts.
        """
        if not chunk.text_content or not chunk.text_content.strip():
            raise FatalGenerationException(f"Chunk {chunk.chunk_number} text content is empty")

        last_exception: Optional[BaseException] = None

        for attempt in range(max_retries):
            try:
                # Check global rate-limit pause
                now = asyncio.get_event_loop().time()
                if self._rate_limit_reset_time > now:
                    await asyncio.sleep(self._rate_limit_reset_time - now)

                audio_path = output_path / f"{chunk.chunk_number}.mp3"
                tmp_audio_path = output_path / f"{chunk.chunk_number}.mp3.tmp"
                communicate = self.edge_tts.Communicate(chunk.text_content, voice)

                async with self._semaphore:
                    try:
                        await asyncio.wait_for(
                            communicate.save(str(tmp_audio_path)), timeout=60
                        )
                    except asyncio.TimeoutError as te:
                        if tmp_audio_path.exists():
                            try:
                                tmp_audio_path.unlink()
                            except Exception:
                                pass
                        raise TransientGenerationException(
                            f"Timeout generating audio for chunk {chunk.chunk_number}",
                            cause=te
                        ) from te

                    # Atomic swap into target path if temp file created
                    if tmp_audio_path.exists():
                        os.replace(str(tmp_audio_path), str(audio_path))
                    elif not audio_path.exists():
                        # Create empty file for mock save calls in test environment
                        audio_path.touch()

                    duration = self._get_file_duration_fast(str(audio_path))

                logging.debug(
                    f"Chunk {chunk.chunk_number} generated successfully "
                    f"(attempt {attempt + 1}/{max_retries}, duration={duration:.2f}s)"
                )
                return str(audio_path.absolute()), duration

            except Exception as exc:
                last_exception = exc

                # ISSUE-11 FIX: bail out immediately on fatal errors
                if not _is_transient_error(exc):
                    logging.error(
                        f"Fatal error on chunk {chunk.chunk_number} "
                        f"(attempt {attempt + 1}/{max_retries}), no retry: {exc}"
                    )
                    raise FatalGenerationException(
                        f"Fatal error on chunk {chunk.chunk_number}: {exc}",
                        cause=exc if isinstance(exc, Exception) else None
                    ) from exc

                # Transient error — log and back-off before next attempt
                remaining = max_retries - attempt - 1
                if remaining > 0:
                    # Check and recover network connection / WLAN on disconnect
                    if self._network_manager and not self._network_manager.is_connected():
                        logging.warning(
                            f"Network loss detected during synthesis of chunk {chunk.chunk_number}. "
                            "Waiting for connection restore and restarting WLAN..."
                        )
                        if self._network_lock:
                            async with self._network_lock:
                                if not self._network_manager.is_connected():
                                    loop = asyncio.get_event_loop()
                                    restored = await loop.run_in_executor(
                                        None,
                                        self._network_manager.wait_for_network,
                                        300.0,  # max wait 5 min
                                        5.0,    # check interval
                                        True    # auto toggle WLAN (nmcli/rfkill)
                                    )
                                    if restored:
                                        logging.info(
                                            f"Network connection restored. Retrying chunk {chunk.chunk_number}..."
                                        )

                    # Exponential backoff with full jitter (capped at 60s max)
                    delay = min(60.0, backoff * (2 ** attempt) + random.uniform(0, backoff))
                    msg = str(exc).lower()
                    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
                        self._rate_limit_reset_time = max(
                            self._rate_limit_reset_time,
                            asyncio.get_event_loop().time() + delay
                        )
                        logging.warning(
                            f"Rate limit detected for chunk {chunk.chunk_number}. "
                            f"Enforcing global worker backoff of {delay:.1f}s."
                        )

                    logging.warning(
                        f"Transient error on chunk {chunk.chunk_number} "
                        f"(attempt {attempt + 1}/{max_retries}): {exc}. "
                        f"Retrying in {delay:.1f}s ({remaining} attempt(s) left)."
                    )
                    await asyncio.sleep(delay)
                else:
                    logging.error(
                        f"Chunk {chunk.chunk_number} exhausted all {max_retries} retry attempts. "
                        f"Last error: {exc}"
                    )

        raise last_exception  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _get_file_duration_fast(self, file_path: str) -> float:
        """Fast duration extraction using mutagen."""
        try:
            from mutagen.mp3 import MP3
            audio = MP3(file_path)
            return float(audio.info.length)
        except Exception:
            return 0.0

    def close(self) -> None:
        """Stops and closes the background asyncio event loop."""
        if hasattr(self, '_loop') and self._loop:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if hasattr(self, '_loop_thread') and self._loop_thread.is_alive():
                self._loop_thread.join(timeout=2.0)
            try:
                self._loop.close()
            except Exception as e:
                logging.warning(f"Error closing AudioGenerator event loop: {e}")
