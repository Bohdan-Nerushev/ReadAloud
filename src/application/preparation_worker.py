"""Background text preparation: read the input file, clean, chunk and store the chunks."""

import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator, List

from PyQt6.QtCore import QThread, pyqtSignal

from src.domain.models import AudioChunk, ProjectConfig
from src.domain.text_chunker import TextChunker
from src.domain.text_processor import TextProcessor
from src.infrastructure.disk_space import ensure_free_space
from src.infrastructure.file_manager import FileManager
from src.infrastructure.logging_config import set_correlation_id

READ_BLOCK_BYTES = 10 * 1024 * 1024
SAVE_WORKER_THREADS = 4


class PreparationWorker(QThread):
    """
    Worker thread that reads the file, processes the text and splits it into chunks
    so the UI thread never blocks.

    The chunk list (text included) stays in memory: the generators take their text from
    it. Only the raw file is streamed in blocks, never held as one string.
    """
    finished = pyqtSignal(list, str, str)  # chunks, text_dir, audio_dir
    error = pyqtSignal(str)

    def __init__(
            self,
            task_id: str,
            config: ProjectConfig,
            file_manager: FileManager,
            text_processor: TextProcessor,
            text_chunker: TextChunker
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.config = config
        self.file_manager = file_manager
        self.text_processor = text_processor
        self.text_chunker = text_chunker

    @staticmethod
    def resolve_workspace_dir(config: ProjectConfig) -> str:
        """
        Where temporary text/audio directories are created.

        Never the input file's directory (it may be read-only media, and the user's
        folder should not be cluttered): the output directory if writable, otherwise
        the system temp directory.
        """
        output_dir = Path(config.output_dir_path).expanduser() if config.output_dir_path else None
        if output_dir is not None and output_dir.is_dir() and os.access(output_dir, os.W_OK | os.X_OK):
            return str(output_dir)
        return tempfile.gettempdir()

    def run(self) -> None:
        """Runs the preparation; emits ``finished`` or ``error`` (nothing when interrupted)."""
        set_correlation_id(self.task_id)
        try:
            logging.info(f"Starting preparation for input: {self.config.input_file_path}")
            chunks = self._read_and_chunk()
            if chunks is None:
                return
            if not chunks:
                raise ValueError("No text chunks generated from input file.")

            workspace_dir = self.resolve_workspace_dir(self.config)
            ensure_free_space(
                sum(len(c.text_content) for c in chunks),
                workspace_dir,
                self.config.output_dir_path,
            )
            text_dir = self.file_manager.create_timestamped_dir("text", workspace_dir)
            audio_dir = self.file_manager.create_timestamped_dir("audio", workspace_dir)

            if not self._save_chunks(chunks, text_dir) or self.isInterruptionRequested():
                return

            logging.info(f"Preparation complete. Generated {len(chunks)} chunks.")
            self.finished.emit(chunks, text_dir, audio_dir)
        except Exception as e:
            logging.error(f"Preparation failed: {e}", exc_info=True)
            self.error.emit(str(e))

    def _read_and_chunk(self):
        """Returns the numbered chunks of the whole file, or None if interrupted."""
        chunks: List[AudioChunk] = []
        for text in self._iter_text_blocks():
            if self.isInterruptionRequested():
                return None
            self._append_chunks(chunks, text)
        return chunks

    def _iter_text_blocks(self) -> Iterator[str]:
        """Yields the file in ~10 MB blocks that never end in the middle of a word."""
        remaining = ""
        with open(self.config.input_file_path, 'r', encoding='utf-8') as f:
            while True:
                block = f.read(READ_BLOCK_BYTES)
                if not block:
                    break
                text = remaining + block
                remaining = ""
                if len(block) == READ_BLOCK_BYTES:
                    last_space = max(text.rfind(' '), text.rfind('\n'))
                    if last_space != -1:
                        text, remaining = text[:last_space], text[last_space:]
                yield text
        if remaining:
            yield remaining

    def _append_chunks(self, chunks: List[AudioChunk], text: str) -> None:
        """Processes *text* and appends its chunks, continuing the global numbering."""
        if not text.strip():
            return
        processed = self.text_processor.process_text(text)
        if not processed.strip():
            return
        for c in self.text_chunker.chunk_text(processed):
            # AudioChunk is frozen, so renumber by creating a new one.
            chunks.append(AudioChunk(chunk_number=len(chunks) + 1, text_content=c.text_content))

    def _save_chunks(self, chunks: List[AudioChunk], text_dir: str) -> bool:
        """Writes the chunk texts in parallel. Returns False if interrupted."""
        executor = ThreadPoolExecutor(max_workers=SAVE_WORKER_THREADS)
        interrupted = False
        try:
            futures = []
            for chunk in chunks:
                if self.isInterruptionRequested():
                    interrupted = True
                    break
                futures.append(executor.submit(self.file_manager.save_text_chunk, chunk, text_dir))
            if not interrupted:
                for future in futures:
                    if self.isInterruptionRequested():
                        interrupted = True
                        break
                    future.result()
        finally:
            executor.shutdown(wait=not interrupted, cancel_futures=interrupted)
        return not interrupted
