"""
Coqui XTTS-v2 audio generator.

Synthesises audio locally via the Coqui TTS library with XTTS-v2 model.
Output is 24 kHz 16-bit mono WAV, converted to MP3 (192 kbps) via pydub
so the rest of the pipeline remains format-agnostic.

Design decisions:
  - torch and TTS are imported lazily (inside _ensure_model_loaded) so that
    this module can be imported even if these packages are not installed.
    ImportError is converted to XttsNotAvailableException at load time.
  - The model is loaded once and reused across calls (singleton per instance).
    A threading.Lock prevents concurrent load attempts.
  - XTTS-v2 requires a speaker reference WAV for voice cloning. The path is
    resolved by XttsModelManager for each (language, gender) pair.
  - Long texts are pre-split by XttsSentenceChunker into segments <=200 chars.
    Each segment is synthesised separately; numpy silence arrays are inserted
    between them before combining into a single WAV file.
  - CUDA OOM is caught and converted to XttsOutOfMemoryException. The model
    is unloaded and GPU cache is flushed to prevent follow-up failures.
  - The public interface (TtsGeneratorProtocol) is identical to AudioGenerator
    and PiperAudioGenerator so GenerationService can use any backend transparently.
"""

import io
import logging
import os
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from src.domain.exceptions import (
    FatalGenerationException,
    TransientGenerationException,
    XttsNotAvailableException,
    XttsOutOfMemoryException,
    XttsReferenceAudioMissingException,
)
from src.domain.models import AudioChunk
from src.domain.xtts_sentence_chunker import XttsSentenceChunker
from src.infrastructure.xtts_model_manager import (
    XTTS_LANGUAGE_MAP,
    XTTS_MODEL_NAME,
    XTTS_SUPPORTED_LANGUAGES,
    XttsModelManager,
)

logger = logging.getLogger(__name__)

# Synthesis sample rate used by XTTS-v2.
XTTS_SAMPLE_RATE: int = 24000

# MP3 bitrate for the final output file.
MP3_BITRATE: str = "192k"

# Timeout in seconds for a single segment synthesis call.
# Complex words or long segments may take 20-60 s on GPU.
_SEGMENT_SYNTHESIS_TIMEOUT: float = 120.0

# Maximum retries for transient errors per chunk.
_DEFAULT_MAX_RETRIES: int = 3

# Base back-off delay in seconds.
_DEFAULT_BACKOFF: float = 2.0


class XttsAudioGenerator:
    """
    TTS generator that synthesises audio locally via Coqui XTTS-v2.

    Public interface is intentionally identical to AudioGenerator and
    PiperAudioGenerator so GenerationService can substitute any backend.

    Thread-safety:
        _model_lock ensures the model is loaded exactly once even if
        generate_audio_batch is called concurrently from multiple threads.
        Synthesis itself is not parallelised — XTTS-v2 is GPU-bound and
        concurrent calls would cause CUDA OOM.
    """

    def __init__(
            self,
            model_manager: XttsModelManager,
            synthesis_timeout: float = _SEGMENT_SYNTHESIS_TIMEOUT,
            silence_between_segments_ms: int = 180,
            max_segment_chars: int = 160,
    ) -> None:
        """
        Args:
            model_manager:               Provides diagnostics and speaker WAV resolution.
            synthesis_timeout:           Seconds to wait for a single segment synthesis.
            silence_between_segments_ms: Milliseconds of silence inserted between segments.
            max_segment_chars:           Hard character limit per XTTS segment.
        """
        self._model_manager = model_manager
        self._synthesis_timeout = synthesis_timeout
        self._silence_ms = silence_between_segments_ms
        self._chunker = XttsSentenceChunker(
            max_chars=max_segment_chars,
            silence_ms=silence_between_segments_ms,
        )

        self._model_lock = threading.Lock()
        self._model = None  # type: Optional[object]  # TTS instance, loaded lazily
        self._model_loaded: bool = False

    # ------------------------------------------------------------------
    # TtsGeneratorProtocol implementation
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
            max_workers: int = 1,
            max_retries: int = _DEFAULT_MAX_RETRIES,
            backoff: float = _DEFAULT_BACKOFF,
    ) -> List[Tuple[str, float]]:
        """
        Generates MP3 audio files for a list of chunks using XTTS-v2.

        Args:
            chunks:         Chunks to synthesise.
            language:       ISO language code (must be in XTTS_SUPPORTED_LANGUAGES).
            gender:         "male" or "female".
            output_dir:     Directory where .mp3 files will be written.
            chunk_callback: Optional callable(chunk_number, file_path, duration).
            max_workers:    Ignored for XTTS (always 1, GPU-bound); kept for parity.
            max_retries:    Maximum retry attempts per chunk.
            backoff:        Base back-off delay in seconds.

        Returns:
            List of (file_path, duration) tuples aligned to *chunks*.
            Entries for failed chunks contain ``None``.

        Raises:
            ValueError: If language is not in XTTS_SUPPORTED_LANGUAGES.
            XttsNotAvailableException: If the TTS package or CUDA is missing.
        """
        if not chunks:
            return []

        if language not in XTTS_SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Language '{language}' is not supported by XTTS. "
                f"Supported: {sorted(XTTS_SUPPORTED_LANGUAGES)}"
            )

        xtts_language = XTTS_LANGUAGE_MAP[language]
        logger.info(
            "[XTTS] generate_audio_batch called: backend=XTTS-v2, chunks=%d, language='%s' -> xtts_lang='%s', "
            "gender='%s', output_dir='%s', max_retries=%d",
            len(chunks), language, xtts_language, gender, output_dir, max_retries,
        )

        speaker_wav = self._model_manager.get_reference_audio_path(language, gender)
        logger.info("[XTTS] Using speaker reference WAV: %s", speaker_wav)

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        self._ensure_model_loaded()

        results: List[Optional[Tuple[str, float]]] = [None] * len(chunks)

        for idx, chunk in enumerate(chunks):
            logger.debug(
                "[XTTS] Synthesising chunk %d/%d (chunk_number=%d, chars=%d)...",
                idx + 1, len(chunks), chunk.chunk_number, len(chunk.text_content),
            )
            try:
                result = self._generate_one_with_retry(
                    chunk=chunk,
                    xtts_language=xtts_language,
                    speaker_wav=speaker_wav,
                    output_path=output_path,
                    max_retries=max_retries,
                    backoff=backoff,
                )
                results[idx] = result
                logger.info(
                    "[XTTS] Chunk %d/%d done: path='%s', duration=%.2fs",
                    idx + 1, len(chunks), result[0], result[1],
                )
                if chunk_callback:
                    try:
                        chunk_callback(chunk.chunk_number, result[0], result[1])
                    except Exception as cb_err:
                        logger.error(
                            "[XTTS] Error in chunk callback for chunk %d: %s",
                            chunk.chunk_number, cb_err, exc_info=True,
                        )
            except Exception as exc:
                logger.error(
                    "[XTTS] Chunk %d/%d (chunk_number=%d) failed permanently: %s",
                    idx + 1, len(chunks), chunk.chunk_number, exc, exc_info=True,
                )
                results[idx] = None

        successful = sum(1 for r in results if r is not None)
        logger.info(
            "[XTTS] Batch complete: %d/%d chunks synthesised successfully.",
            successful, len(chunks),
        )
        return results

    def close(self) -> None:
        """Unloads the model and releases GPU memory."""
        self.unload_model()

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def _ensure_model_loaded(self) -> None:
        """
        Loads the XTTS-v2 model on first call. Thread-safe via _model_lock.

        Raises:
            XttsNotAvailableException: If the TTS package or CUDA is unavailable.
        """
        if self._model_loaded:
            return
        with self._model_lock:
            if self._model_loaded:
                return
            logger.info(
                "[XTTS] Loading XTTS-v2 model onto GPU (FP16). This may take 30-60 s. "
                "Backend: XTTS-v2 (Local GPU)."
            )
            try:
                from TTS.api import TTS  # noqa: PLC0415 — intentional lazy import
                import torch
                cuda_available = torch.cuda.is_available()
                device_name = torch.cuda.get_device_name(0) if cuda_available else "CPU (no CUDA)"
                logger.info(
                    "[XTTS] CUDA available: %s, device: %s, model: %s, gpu_mode: %s",
                    cuda_available, device_name, XTTS_MODEL_NAME, cuda_available,
                )
                self._model = TTS(
                    model_name=XTTS_MODEL_NAME,
                    progress_bar=False,
                    gpu=cuda_available,
                )
                self._model_loaded = True
                if cuda_available:
                    try:
                        free_bytes, total_bytes = torch.cuda.mem_get_info(device=0)
                        _gb = 1024 ** 3
                        logger.info(
                            "[XTTS] Model loaded. VRAM after load: %.2f GB free / %.2f GB total.",
                            free_bytes / _gb, total_bytes / _gb,
                        )
                    except Exception as mem_exc:
                        logger.debug("[XTTS] Could not query VRAM after model load: %s", mem_exc)
                else:
                    logger.info("[XTTS] Model loaded on CPU (no CUDA — synthesis will be slow).")
            except ImportError as exc:
                logger.error("[XTTS] ImportError: TTS package not installed. %s", exc)
                raise XttsNotAvailableException(
                    "The 'TTS' package is not installed. "
                    "Run: pip install -r xtts_requirements.txt"
                ) from exc
            except Exception as exc:
                logger.error("[XTTS] Failed to load XTTS-v2 model: %s", exc, exc_info=True)
                raise XttsNotAvailableException(
                    f"Failed to load XTTS-v2 model: {exc}"
                ) from exc

    def unload_model(self) -> None:
        """
        Unloads the model from memory and flushes the CUDA cache.

        Safe to call even if the model was never loaded.
        """
        with self._model_lock:
            self._model = None
            self._model_loaded = False
        self._model_manager.flush_gpu_cache()
        logger.info("XTTS-v2 model unloaded and GPU cache flushed.")

    @property
    def is_model_loaded(self) -> bool:
        """Returns True if the model is currently loaded in GPU memory."""
        return self._model_loaded

    # ------------------------------------------------------------------
    # Single-chunk synthesis with retry
    # ------------------------------------------------------------------

    def _generate_one_with_retry(
            self,
            chunk: AudioChunk,
            xtts_language: str,
            speaker_wav: Path,
            output_path: Path,
            max_retries: int,
            backoff: float,
    ) -> Tuple[str, float]:
        """Synthesises one chunk with exponential back-off retry for transient errors."""
        if not chunk.text_content or not chunk.text_content.strip():
            raise FatalGenerationException(
                f"Chunk {chunk.chunk_number} text content is empty"
            )

        last_exception: Optional[BaseException] = None

        for attempt in range(max_retries):
            try:
                return self._synthesise_chunk(
                    chunk=chunk,
                    xtts_language=xtts_language,
                    speaker_wav=speaker_wav,
                    output_path=output_path,
                )
            except XttsReferenceAudioMissingException:
                # Fatal — retrying will not fix a missing file.
                raise
            except XttsOutOfMemoryException:
                # Fatal — unload the model and propagate immediately.
                self.unload_model()
                raise
            except FatalGenerationException:
                raise
            except Exception as exc:
                last_exception = exc
                remaining = max_retries - attempt - 1
                if remaining > 0:
                    import time
                    delay = min(60.0, backoff * (2 ** attempt))
                    logger.warning(
                        "Transient XTTS error on chunk %d (attempt %d/%d): %s. "
                        "Retrying in %.1fs.",
                        chunk.chunk_number, attempt + 1, max_retries, exc, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "Chunk %d exhausted all %d XTTS retry attempts. Last error: %s",
                        chunk.chunk_number, max_retries, exc,
                    )

        raise TransientGenerationException(
            f"XTTS chunk {chunk.chunk_number} failed after {max_retries} attempt(s): "
            f"{last_exception}",
        )

    # ------------------------------------------------------------------
    # Core synthesis pipeline
    # ------------------------------------------------------------------

    def _synthesise_chunk(
            self,
            chunk: AudioChunk,
            xtts_language: str,
            speaker_wav: Path,
            output_path: Path,
    ) -> Tuple[str, float]:
        """
        Synthesises one AudioChunk:
          1. Split text into XTTS-safe segments.
          2. Synthesise each segment to a numpy array.
          3. Interleave with silence arrays.
          4. Write combined WAV (24 kHz mono 16-bit).
          5. Convert WAV -> MP3 (192 kbps).
          6. Return (mp3_path, duration_seconds).
        """
        try:
            import numpy as np
            import soundfile as sf
        except ImportError as exc:
            raise XttsNotAvailableException(
                "Required packages 'numpy' and 'soundfile' are not installed."
            ) from exc

        segments = self._chunker.split(chunk.text_content)

        audio_arrays = []
        silence_samples = int(self._silence_ms / 1000.0 * XTTS_SAMPLE_RATE)
        silence = np.zeros(silence_samples, dtype=np.float32)

        for i, segment in enumerate(segments):
            segment_audio = self._synthesise_segment(
                text=segment.text,
                language=xtts_language,
                speaker_wav=str(speaker_wav),
            )
            audio_arrays.append(np.array(segment_audio, dtype=np.float32))
            # Insert silence between segments (not after the last one).
            if segment.silence_after_ms > 0 and i < len(segments) - 1:
                audio_arrays.append(silence)

        combined = np.concatenate(audio_arrays) if len(audio_arrays) > 1 else audio_arrays[0]

        mp3_tmp = output_path / f"{chunk.chunk_number}.mp3.tmp"
        mp3_path = output_path / f"{chunk.chunk_number}.mp3"

        try:
            # Write 16-bit PCM WAV at 24 kHz into in-memory buffer to avoid disk I/O.
            wav_bio = io.BytesIO()
            sf.write(wav_bio, combined, XTTS_SAMPLE_RATE, format="WAV", subtype="PCM_16")
            wav_bio.seek(0)
            self._convert_wav_bytes_to_mp3(wav_bio, mp3_tmp)
            os.replace(str(mp3_tmp), str(mp3_path))

            duration = self._get_mp3_duration(str(mp3_path))
            logger.debug(
                "Chunk %d synthesised via XTTS (%d segment(s), duration=%.2fs)",
                chunk.chunk_number, len(segments), duration,
            )
            return str(mp3_path.absolute()), duration
        finally:
            self._cleanup_tmp_files(mp3_tmp)

    def _synthesise_segment(
            self,
            text: str,
            language: str,
            speaker_wav: str,
    ) -> list:
        """
        Calls TTS.tts() for a single text segment.

        Returns a list of float samples at XTTS_SAMPLE_RATE.

        Raises:
            XttsOutOfMemoryException: On CUDA OOM.
        """
        import numpy as np
        import re

        # Guard 1: if segment contains no speakable characters, return short silence
        if not any(c.isalnum() for c in text):
            logger.warning("[XTTS] Segment text has no speakable characters: %r. Returning silence.", text)
            return np.zeros(int(0.2 * XTTS_SAMPLE_RATE), dtype=np.float32).tolist()

        # Guard 2: auto-fallback language if Cyrillic text passed in non-Cyrillic mode (e.g., lang='en')
        effective_language = language
        if language not in ("uk", "ru") and re.search(r"[а-яА-ЯіЇїЄєҐґ]", text):
            effective_language = "uk"
            logger.info(
                "[XTTS] Detected Cyrillic script in text while language was '%s'. Automatically using language='uk' for segment: %r",
                language, text[:40],
            )

        logger.debug(
            "[XTTS] _synthesise_segment: lang='%s' (effective='%s'), chars=%d, speaker_wav='%s'",
            language, effective_language, len(text), speaker_wav,
        )

        try:
            try:
                import torch
                with torch.inference_mode():
                    audio = self._model.tts(  # type: ignore[union-attr]
                        text=text,
                        speaker_wav=speaker_wav,
                        language=effective_language,
                    )
            except ImportError:
                audio = self._model.tts(  # type: ignore[union-attr]
                    text=text,
                    speaker_wav=speaker_wav,
                    language=effective_language,
                )

            if audio is None or len(audio) == 0:
                logger.warning(
                    "[XTTS] Model returned empty audio for text=%r (lang='%s'). Substituting silence.",
                    text[:40], effective_language,
                )
                return np.zeros(int(0.2 * XTTS_SAMPLE_RATE), dtype=np.float32).tolist()

            logger.debug("[XTTS] _synthesise_segment: got %d samples.", len(audio))
            return audio

        except Exception as exc:
            exc_str = str(exc)
            is_oom = "out of memory" in exc_str.lower() or (
                "cuda" in exc_str.lower() and "memory" in exc_str.lower()
            )
            if is_oom:
                logger.error(
                    "[XTTS] CUDA OOM during segment synthesis (chars=%d). "
                    "Unloading model to prevent follow-up failures. Error: %s",
                    len(text), exc,
                )
                raise XttsOutOfMemoryException(
                    f"CUDA Out-Of-Memory during XTTS segment synthesis. "
                    f"Segment length: {len(text)} chars. "
                    f"Try reducing max_segment_chars or freeing GPU memory. "
                    f"Original error: {exc}"
                ) from exc

            # Non-OOM exception (e.g. text cleaning error, unsupported token, phonemizer failure)
            logger.warning(
                "[XTTS] Non-OOM exception synthesising segment (text=%r, lang='%s'): %s. Substituting silence.",
                text[:40], effective_language, exc,
            )
            return np.zeros(int(0.2 * XTTS_SAMPLE_RATE), dtype=np.float32).tolist()

    # ------------------------------------------------------------------
    # WAV -> MP3 conversion and duration extraction
    # ------------------------------------------------------------------

    def _convert_wav_bytes_to_mp3(self, wav_bytes: io.BytesIO, mp3_tmp_path: Path) -> None:
        """Converts an in-memory WAV buffer to MP3 using pydub."""
        from pydub import AudioSegment
        audio = AudioSegment.from_file(wav_bytes, format="wav")
        audio.export(str(mp3_tmp_path), format="mp3", bitrate=MP3_BITRATE)

    def _convert_wav_to_mp3(self, wav_path: Path, mp3_tmp_path: Path) -> None:
        """
        Converts a WAV file to MP3 using pydub (wraps ffmpeg).
        Removes the temporary WAV regardless of conversion outcome.
        """
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(str(wav_path))
            audio.export(str(mp3_tmp_path), format="mp3", bitrate=MP3_BITRATE)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _get_mp3_duration(self, file_path: str) -> float:
        """Returns MP3 duration in seconds using mutagen."""
        try:
            from mutagen.mp3 import MP3
            return float(MP3(file_path).info.length)
        except Exception:
            return 0.0

    def _get_file_duration_fast(self, file_path: str) -> float:
        """Alias for _get_mp3_duration — provides a uniform interface across all TTS generators."""
        return self._get_mp3_duration(file_path)

    @staticmethod
    def _cleanup_tmp_files(*paths: Optional[Path]) -> None:
        """Silently removes temporary files to avoid leaving stale artefacts."""
        for path in paths:
            if path is not None:
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
