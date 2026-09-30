"""
TTS Generator Protocol.

Defines the structural interface that all TTS backend implementations must satisfy.
Using Protocol (PEP 544) allows duck-typing; ``BaseAudioGenerator`` supplies the shared
defaults (no-op model lifecycle, pause/cancel plumbing) so engines only add what differs.
"""

from typing import Callable, List, Optional, Protocol, Tuple, runtime_checkable

from src.domain.models import AudioChunk
from src.domain.retry_budget import RetryBudget


@runtime_checkable
class TtsGeneratorProtocol(Protocol):
    """
    Structural interface for TTS audio generators.

    Any class that provides these methods is considered a valid generator,
    regardless of inheritance. Edge, Piper and XTTS generators all satisfy it.
    """

    def generate_audio(
            self,
            chunk: AudioChunk,
            language: str,
            gender: str,
            output_dir: str,
    ) -> Tuple[str, float]:
        """
        Generates audio for a single text chunk.

        Args:
            chunk:      The text chunk to synthesise.
            language:   ISO language code (e.g. "ru", "en").
            gender:     "male" or "female".
            output_dir: Directory where the audio file will be written.

        Returns:
            Tuple of (absolute_file_path, duration_in_seconds).
        """
        ...

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
        Generates audio files for a list of chunks.

        Args:
            chunks:          Chunks to synthesise.
            language:        ISO language code.
            gender:          "male" or "female".
            output_dir:      Directory where audio files will be written.
            chunk_callback:  Optional callable(chunk_number, file_path, duration)
                             invoked immediately after each chunk succeeds.
            max_workers:     Maximum number of concurrent synthesis workers.
            max_retries:     Maximum retry attempts per chunk.
            backoff:         Base back-off delay in seconds.

        Returns:
            List of (file_path, duration) tuples aligned to *chunks*.
            Entries for failed chunks contain ``None``.
        """
        ...

    def close(self) -> None:
        """Releases resources held by this generator (threads, connections, etc.)."""
        ...

    last_error: Optional[str]

    @property
    def is_model_loaded(self) -> bool:
        """True while the engine keeps a model in memory (always False for Edge/Piper)."""
        ...

    def unload_model(self) -> None:
        """Releases the resident model, if any (no-op for Edge/Piper)."""
        ...

    def pause(self) -> None:
        """Holds back chunks that have not started yet."""
        ...

    def resume(self) -> None:
        """Releases chunks held back by ``pause``."""
        ...

    def cancel(self) -> None:
        """Aborts running batches as soon as possible."""
        ...

    def set_retry_budget(self, budget: Optional[RetryBudget]) -> None:
        """Installs the shared retry allowance of the running generation."""
        ...
