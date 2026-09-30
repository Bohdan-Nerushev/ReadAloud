"""Rebuilds a task's chunks and progress from the files a previous run left on disk."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.domain.models import AudioChunk


@dataclass
class RestoredProgress:
    chunks: List[AudioChunk]
    audio_files: List[Optional[str]]


def load_chunks(text_dir: str) -> Optional[List[AudioChunk]]:
    """Reads ``<n>.txt`` chunk files in numeric order; None if there are none or one is unreadable."""
    numbered = []
    for f in Path(text_dir).glob("*.txt"):
        try:
            numbered.append((int(f.stem), f))
        except ValueError:
            continue
    if not numbered:
        return None

    chunks: List[AudioChunk] = []
    for num, f in sorted(numbered, key=lambda item: item[0]):
        try:
            content = f.read_text(encoding="utf-8")
        except Exception as e:
            logging.error(f"Failed to read restored chunk file {f}: {e}")
            return None
        chunks.append(AudioChunk(chunk_number=num, text_content=content, text_file_path=str(f.absolute())))
    return chunks


def _remove_corrupt(path: Path, what: str) -> None:
    try:
        path.unlink()
        logging.warning(f"Removed corrupt/empty {what} file on restore: {path}")
    except Exception as e:
        logging.error(f"Failed to remove corrupt {what} file {path}: {e}")


class ProgressRestorer:
    """
    Verifies the audio already on disk and feeds it to the assembly service.

    Corrupt chunk or part files are deleted so they are regenerated; a finished batch
    without a valid part file is re-assembled.
    """

    def __init__(self, assembly_service, duration_reader: Callable[[str], float]) -> None:
        self._assembly = assembly_service
        self._read_duration = duration_reader

    def restore(self, chunks: List[AudioChunk], audio_dir: str, speed: float, task_id: str) -> RestoredProgress:
        audio_path = Path(audio_dir)
        audio_files: List[Optional[str]] = [None] * len(chunks)
        chunks = list(chunks)
        self._assembly.reset(len(chunks))

        for idx, chunk in enumerate(chunks):
            chunk_audio = audio_path / f"{chunk.chunk_number}.mp3"
            if not (chunk_audio.exists() and chunk_audio.stat().st_size > 0):
                continue
            duration = self._read_duration(str(chunk_audio))
            if duration <= 0.0:
                _remove_corrupt(chunk_audio, "chunk")
                continue
            audio_files[idx] = str(chunk_audio.absolute())
            chunks[idx] = chunk.with_audio_path(str(chunk_audio.absolute()), duration)
            batch_index = self._assembly.mark_chunk_ready(idx, duration)
            if batch_index is not None:
                self._restore_batch(batch_index, audio_path, audio_dir, audio_files, speed, task_id)
        return RestoredProgress(chunks, audio_files)

    def _restore_batch(self, batch_index: int, audio_path: Path, audio_dir: str,
                       audio_files: List[Optional[str]], speed: float, task_id: str) -> None:
        part_path = audio_path / f"part_{batch_index}.mp3"
        valid = part_path.exists() and part_path.stat().st_size > 0 and self._read_duration(str(part_path)) > 0.0
        if valid:
            self._assembly.restore_batch_result(batch_index, str(part_path))
            return
        if part_path.exists():
            _remove_corrupt(part_path, "batch part")
        size = self._assembly.BATCH_SIZE
        start = batch_index * size
        self._assembly.submit_batch_by_index(
            batch_index, audio_files[start:min(start + size, len(audio_files))], audio_dir, speed, task_id
        )
