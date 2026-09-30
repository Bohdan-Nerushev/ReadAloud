"""Behaviour shared by every TTS generator (Edge, Piper, XTTS)."""

from pathlib import Path
from typing import List, Optional, Tuple

from src.domain.audio_utils import get_mp3_duration
from src.domain.cancellable_batch import CancellableBatchMixin
from src.domain.models import AudioChunk
from src.domain.retry_budget import RetryBudget

DEFAULT_MP3_BITRATE = "128k"


class BaseAudioGenerator(CancellableBatchMixin):
    """
    Common base of the generators: single-chunk convenience call, MP3 helpers,
    shared retry budget, and no-op model lifecycle hooks.

    Engines without a resident model (Edge, Piper) inherit the no-op
    ``unload_model`` / ``is_model_loaded``, so callers never branch on the backend.
    """

    MP3_BITRATE = DEFAULT_MP3_BITRATE

    _retry_budget: Optional[RetryBudget] = None

    def generate_audio(
            self,
            chunk: AudioChunk,
            language: str,
            gender: str,
            output_dir: str,
    ) -> Tuple[str, float]:
        """Generates audio for a single chunk. Delegates to ``generate_audio_batch``."""
        results: List = self.generate_audio_batch([chunk], language, gender, output_dir)  # type: ignore[attr-defined]
        return results[0]

    def close(self) -> None:
        """Releases resources held by the generator (overridden where there are any)."""

    # -- retry budget ---------------------------------------------------

    def set_retry_budget(self, budget: Optional[RetryBudget]) -> None:
        """Installs the retry budget of the running generation (None = unlimited)."""
        self._retry_budget = budget

    def _may_retry(self) -> bool:
        """True if another retry is allowed by the shared budget; consumes one unit."""
        budget = self._retry_budget
        return budget is None or budget.try_consume()

    # -- model lifecycle (no-op by default) -----------------------------

    def unload_model(self) -> None:
        """Releases a resident model, if the engine has one."""

    @property
    def is_model_loaded(self) -> bool:
        return False

    # -- file helpers ---------------------------------------------------

    def _get_mp3_duration(self, file_path: str) -> float:
        """Returns MP3 duration in seconds (0.0 if unreadable)."""
        return get_mp3_duration(file_path)

    def _get_file_duration_fast(self, file_path: str) -> float:
        return self._get_mp3_duration(file_path)

    def _convert_wav_to_mp3(self, wav_path: Path, mp3_tmp_path: Path) -> None:
        """Encodes a WAV file to MP3 with pydub and always removes the source WAV."""
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(str(wav_path))
            audio.export(str(mp3_tmp_path), format="mp3", bitrate=self.MP3_BITRATE)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _cleanup_tmp_files(*paths: Optional[Path]) -> None:
        """Silently removes temporary files so no stale artefacts remain."""
        for path in paths:
            if path is not None:
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
