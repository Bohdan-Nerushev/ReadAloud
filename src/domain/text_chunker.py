"""
Text chunking service.

Splits processed text into chunks for audio generation. Chunks end on sentence
(or paragraph) boundaries whenever possible so that intonation and pauses are
not broken in the middle of a sentence.
"""

import re
from typing import List

from src.domain.models import AudioChunk

# A sentence ends at ., !, ? or … (plus closing quotes/brackets) followed by
# whitespace, or at a line break. The trailing whitespace stays with the sentence.
_SENTENCE_END = re.compile(r'[.!?\u2026]+[\'")\]\u00bb\u201d]*\s+|\n+')
_CLAUSE_END = re.compile(r'[,;:\u2014\u2013]\s+')
_WORD = re.compile(r'\S+\s*')

# A sentence up to this multiple of chunk_size is kept whole in its own chunk.
SOFT_LIMIT_FACTOR = 1.25


class TextChunker:
    """
    Splits text into chunks of at most ~chunk_size characters.

    Sentences are packed greedily. A sentence that does not fit starts a new chunk;
    only a sentence much longer than chunk_size is split, preferably at a clause
    boundary (, ; :), otherwise at a word boundary. Words are never cut unless a
    single word is longer than chunk_size.
    """

    def __init__(self) -> None:
        """Initialize the TextChunker."""

    def chunk_text(self, text: str, chunk_size: int = 1000) -> List[AudioChunk]:
        """
        Args:
            text: The text to split into chunks
            chunk_size: Target maximum size of a chunk in characters

        Returns:
            AudioChunk objects numbered sequentially from 1 (whitespace-only pieces dropped)

        Raises:
            ValueError: If text is empty or chunk_size is not positive
        """
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")
        if chunk_size <= 0:
            raise ValueError(f"Chunk size must be positive, got: {chunk_size}")

        soft_max = int(chunk_size * SOFT_LIMIT_FACTOR)
        pieces: List[str] = []
        for sentence in self._split(text, _SENTENCE_END):
            if len(sentence) <= soft_max:
                pieces.append(sentence)
            else:
                pieces.extend(self._split_long(sentence, chunk_size))

        chunks: List[str] = []
        buffer = ""
        for piece in pieces:
            if buffer and len(buffer) + len(piece) > chunk_size:
                chunks.append(buffer)
                buffer = ""
            buffer += piece
        if buffer:
            chunks.append(buffer)

        return [
            AudioChunk(chunk_number=i, text_content=c)
            for i, c in enumerate((c for c in chunks if c.strip()), start=1)
        ]

    @staticmethod
    def _split(text: str, pattern: "re.Pattern[str]") -> List[str]:
        parts, start = [], 0
        for m in pattern.finditer(text):
            parts.append(text[start:m.end()])
            start = m.end()
        if start < len(text):
            parts.append(text[start:])
        return parts

    def _split_long(self, sentence: str, limit: int) -> List[str]:
        """Splits an over-long sentence at clause, then word, then character level."""
        result: List[str] = []
        buffer = ""
        for clause in self._split(sentence, _CLAUSE_END):
            for piece in ([clause] if len(clause) <= limit else self._split_words(clause, limit)):
                if buffer and len(buffer) + len(piece) > limit:
                    result.append(buffer)
                    buffer = ""
                buffer += piece
        if buffer:
            result.append(buffer)
        return result

    @staticmethod
    def _split_words(text: str, limit: int) -> List[str]:
        result: List[str] = []
        buffer = ""
        for m in _WORD.finditer(text):
            word = m.group()
            while len(word) > limit:  # a single unbreakable token
                if buffer:
                    result.append(buffer)
                    buffer = ""
                result.append(word[:limit])
                word = word[limit:]
            if buffer and len(buffer) + len(word) > limit:
                result.append(buffer)
                buffer = ""
            buffer += word
        if buffer:
            result.append(buffer)
        return result
