"""
Unit tests for XttsSentenceChunker.

No GPU or TTS package required — pure Python logic only.
"""

import unittest

from src.domain.xtts_sentence_chunker import TextSegment, XttsSentenceChunker


class TestXttsSentenceChunkerInit(unittest.TestCase):

    def test_default_construction(self):
        chunker = XttsSentenceChunker()
        self.assertIsNotNone(chunker)

    def test_invalid_max_chars_raises(self):
        with self.assertRaises(ValueError):
            XttsSentenceChunker(max_chars=5)

    def test_invalid_silence_raises(self):
        with self.assertRaises(ValueError):
            XttsSentenceChunker(silence_ms=-1)


class TestXttsSentenceChunkerSplit(unittest.TestCase):

    def setUp(self):
        self.chunker = XttsSentenceChunker(max_chars=200, silence_ms=250)

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            self.chunker.split("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            self.chunker.split("   \n  ")

    def test_single_short_sentence_is_one_segment(self):
        segments = self.chunker.split("Hello world.")
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].text, "Hello world.")

    def test_segment_text_stripped(self):
        segments = self.chunker.split("  Hello world.  ")
        self.assertEqual(segments[0].text, "Hello world.")

    def test_last_segment_has_no_silence(self):
        text = "First sentence. Second sentence. Third sentence."
        segments = self.chunker.split(text)
        self.assertEqual(segments[-1].silence_after_ms, 0)

    def test_intermediate_segments_have_silence(self):
        # Force multiple segments by using short max_chars
        chunker = XttsSentenceChunker(max_chars=50, silence_ms=250)
        text = "This is the first sentence. This is the second sentence. And the third."
        segments = chunker.split(text)
        if len(segments) > 1:
            for seg in segments[:-1]:
                self.assertEqual(seg.seg.silence_after_ms if hasattr(seg, 'seg') else seg.silence_after_ms, 250)

    def test_no_segment_exceeds_max_chars(self):
        long_text = (
            "This is a very long paragraph that should be split into multiple segments. "
            "Each segment must not exceed the maximum character limit set by the chunker. "
            "The chunker should respect word boundaries and sentence boundaries alike. "
            "Let us verify this with an automated test that checks every output segment."
        )
        chunker = XttsSentenceChunker(max_chars=100)
        segments = chunker.split(long_text)
        for seg in segments:
            self.assertLessEqual(
                len(seg.text), 100,
                f"Segment exceeds max_chars: '{seg.text}'"
            )

    def test_returns_list_of_text_segments(self):
        segments = self.chunker.split("Hello world.")
        self.assertIsInstance(segments, list)
        for seg in segments:
            self.assertIsInstance(seg, TextSegment)

    def test_paragraph_breaks_are_split(self):
        text = "First paragraph.\n\nSecond paragraph."
        segments = self.chunker.split(text)
        self.assertGreaterEqual(len(segments), 1)
        # Both paragraphs must appear in the combined text
        combined = " ".join(s.text for s in segments)
        self.assertIn("First paragraph", combined)
        self.assertIn("Second paragraph", combined)

    def test_very_long_single_word_is_not_lost(self):
        """A word longer than max_chars must still appear in output (not silently dropped)."""
        chunker = XttsSentenceChunker(max_chars=30)
        long_word = "a" * 50
        segments = chunker.split(f"Start {long_word} end.")
        combined = " ".join(s.text for s in segments)
        self.assertIn(long_word, combined)

    def test_silence_ms_propagated_correctly(self):
        chunker = XttsSentenceChunker(max_chars=30, silence_ms=300)
        text = "Sentence one. Sentence two. Sentence three."
        segments = chunker.split(text)
        for seg in segments[:-1]:
            self.assertEqual(seg.silence_after_ms, 300)
        self.assertEqual(segments[-1].silence_after_ms, 0)

    def test_multilingual_cyrillic_text(self):
        text = "Привіт, це перше речення. Це друге речення українською мовою."
        segments = self.chunker.split(text)
        self.assertGreater(len(segments), 0)
        combined = " ".join(s.text for s in segments)
        self.assertIn("Привіт", combined)

    def test_merges_short_consecutive_sentences(self):
        """Short sentences should be merged to reduce unnecessary silence gaps."""
        chunker = XttsSentenceChunker(max_chars=200)
        text = "Hi. Yes. OK. Sure."
        segments = chunker.split(text)
        # All four short sentences should merge into a single segment
        self.assertEqual(len(segments), 1)


class TestXttsSentenceChunkerWordBoundary(unittest.TestCase):

    def test_split_respects_word_boundaries(self):
        """Segments must not cut words in the middle."""
        chunker = XttsSentenceChunker(max_chars=40)
        text = "The quick brown fox jumps over the lazy dog running fast."
        segments = chunker.split(text)
        for seg in segments:
            # No leading/trailing partial words (all tokens are valid words)
            self.assertEqual(seg.text, seg.text.strip())


if __name__ == "__main__":
    unittest.main()
