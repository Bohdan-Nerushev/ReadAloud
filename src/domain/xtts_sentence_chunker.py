"""
XTTS Sentence Chunker.

XTTS-v2 is highly sensitive to input segment length. Long inputs produce
degraded prosody, skipped words, or audio artifacts. This module provides
a dedicated chunker that:

  1. Splits text into sentences using punctuation heuristics.
  2. Merges short sentences to minimise unnecessary silence gaps.
  3. Enforces a hard character limit per segment (default 200).
  4. Provides silence gap durations (in samples) to interleave between segments.

Design decisions:
  - Sentence splitting is done via regex, not an NLP library, to avoid adding
    heavy dependencies (spaCy, NLTK). Accuracy is sufficient for TTS segmentation.
  - Abbreviations and decimals (e.g. "Mr.", "3.14") are handled by requiring the
    character after "." to be uppercase or whitespace before treating it as a
    sentence boundary.
  - The chunker is stateless and has no external dependencies beyond stdlib.
"""

import re
from dataclasses import dataclass
from typing import List

# Hard limit in characters per XTTS segment. Beyond this, quality degrades.
MAX_SEGMENT_CHARS: int = 160

# Minimum characters for a segment to stand alone; shorter ones are merged.
MIN_MERGE_CHARS: int = 30

# Sentence-boundary pattern: split after . ! ? ; followed by whitespace and
# an uppercase letter, end-of-string, or a closing quote/bracket.
_SENTENCE_BOUNDARY_RE = re.compile(
    r'(?<=[.!?;])\s+(?=[A-ZА-ЯІЇЄA-Z\u0400-\u04FF\"]|$)'
)

# Also split on newlines (paragraph breaks in books).
_NEWLINE_RE = re.compile(r'\n+')


@dataclass(frozen=True)
class TextSegment:
    """A single text segment ready for XTTS synthesis."""
    text: str
    silence_after_ms: int = 180  # milliseconds of silence to append after this segment


class XttsSentenceChunker:
    """
    Splits arbitrary text into XTTS-safe segments with configurable length limits.

    Usage:
        chunker = XttsSentenceChunker()
        segments = chunker.split("Long text here...")
        # segments is a list of TextSegment objects
    """

    def __init__(
            self,
            max_chars: int = MAX_SEGMENT_CHARS,
            silence_ms: int = 180,
    ) -> None:
        """
        Args:
            max_chars:  Hard character limit per segment. Segments exceeding this
                        are split further at word boundaries.
            silence_ms: Milliseconds of silence to insert between segments.
        """
        if max_chars < 10:
            raise ValueError(f"max_chars must be at least 10, got {max_chars}")
        if silence_ms < 0:
            raise ValueError(f"silence_ms must be non-negative, got {silence_ms}")
        self._max_chars = max_chars
        self._silence_ms = silence_ms

    def split(self, text: str) -> List[TextSegment]:
        """
        Splits text into XTTS-safe segments.

        Args:
            text: The input text to segment.

        Returns:
            List of TextSegment objects. The last segment has silence_after_ms=0.

        Raises:
            ValueError: If text is empty or whitespace-only.
        """
        if not text or not text.strip():
            raise ValueError("Input text cannot be empty")

        # Step 1: normalise whitespace, split on paragraph breaks.
        paragraphs = _NEWLINE_RE.split(text.strip())

        # Step 2: split each paragraph into raw sentences.
        raw_sentences: List[str] = []
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            parts = _SENTENCE_BOUNDARY_RE.split(para)
            for part in parts:
                stripped = part.strip()
                if stripped:
                    raw_sentences.append(stripped)

        if not raw_sentences:
            raise ValueError("No processable sentences found in input text")

        # Step 3: merge short sentences and enforce max_chars limit.
        merged = self._merge_sentences(raw_sentences)

        # Filter out sentences that contain no speakable (alphanumeric) characters
        speakable_merged = [s for s in merged if any(c.isalnum() for c in s)]
        if not speakable_merged:
            speakable_merged = ["."]

        # Step 4: wrap into TextSegment objects.
        segments = [
            TextSegment(text=s, silence_after_ms=self._silence_ms if i < len(speakable_merged) - 1 else 0)
            for i, s in enumerate(speakable_merged)
        ]
        return segments

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _merge_sentences(self, sentences: List[str]) -> List[str]:
        """
        Merges short consecutive sentences and splits overly long ones.

        Strategy:
          - Accumulate sentences into a buffer until adding the next one
            would exceed max_chars.
          - If a single sentence already exceeds max_chars, split it at word
            boundaries.
        """
        merged: List[str] = []
        buffer = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            # A single sentence is already over the limit — split it.
            if len(sentence) > self._max_chars:
                # Flush current buffer first.
                if buffer:
                    merged.append(buffer.strip())
                    buffer = ""
                merged.extend(self._split_at_word_boundary(sentence))
                continue

            # Check if appending this sentence to the buffer would overflow.
            candidate = f"{buffer} {sentence}".strip() if buffer else sentence
            if len(candidate) <= self._max_chars:
                buffer = candidate
            else:
                # Flush the buffer and start a new one with this sentence.
                if buffer:
                    merged.append(buffer.strip())
                buffer = sentence

        if buffer:
            merged.append(buffer.strip())

        return [s for s in merged if s]

    def _split_at_word_boundary(self, text: str) -> List[str]:
        """
        Splits a single over-length string into chunks of at most max_chars,
        breaking only at whitespace boundaries.
        """
        words = text.split()
        chunks: List[str] = []
        current = ""

        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if len(candidate) <= self._max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                # Handle a single word longer than max_chars (e.g. URL).
                if len(word) > self._max_chars:
                    chunks.append(word)
                    current = ""
                else:
                    current = word

        if current:
            chunks.append(current)

        return chunks
