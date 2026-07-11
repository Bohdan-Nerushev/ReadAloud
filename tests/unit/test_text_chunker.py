import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.domain.text_chunker import TextChunker

class TestTextChunker(unittest.TestCase):
    def setUp(self):
        self.chunker = TextChunker()

    def test_chunk_text_respects_boundaries(self):
        """test_chunk_text_respects_boundaries: Verifies that text is not split in the middle of a word."""
        text = "word1 word2 word3"
        # chunk_size=7 points to " word2"
        # The algorithm should find the space after "word2" and include it.
        chunks = self.chunker.chunk_text(text, chunk_size=7)
        self.assertEqual(chunks[0].text_content, "word1 word2 ")
        self.assertEqual(chunks[1].text_content, "word3")

    def test_chunk_text_sequencing(self):
        """test_chunk_text_sequencing: Verifies that chunks have sequential numbers starting from 1."""
        text = "This is a longer text to create multiple chunks."
        chunks = self.chunker.chunk_text(text, chunk_size=10)
        for i, chunk in enumerate(chunks):
            self.assertEqual(chunk.chunk_number, i + 1)

    def test_chunk_text_small_size(self):
        """test_chunk_text_small_size: Verifies chunking of short text."""
        text = "Short"
        chunks = self.chunker.chunk_text(text, chunk_size=100)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text_content, "Short")

    def test_chunk_text_exact_multiplier(self):
        """test_chunk_text_exact_multiplier: Verifies chunking when text length is a multiple of chunk size."""
        # "1234 6789 " is 10 chars. chunk_size=5.
        # target_end=5 ("1234 "). text.find(' ', 5) finds no space after pos 5.
        # So it takes the rest of the text.
        text = "1234 6789 "
        chunks = self.chunker.chunk_text(text, chunk_size=5)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].text_content, "1234 6789 ")

    def test_chunk_text_invalid_size(self):
        """test_chunk_text_invalid_size: Verifies ValueError is raised for chunk_size <= 0."""
        with self.assertRaises(ValueError):
            self.chunker.chunk_text("text", chunk_size=0)
        with self.assertRaises(ValueError):
            self.chunker.chunk_text("text", chunk_size=-1)

    def test_chunk_text_empty_string(self):
        """test_chunk_text_empty_string: Verifies ValueError is raised for an empty input string."""
        with self.assertRaises(ValueError):
            self.chunker.chunk_text("")
        with self.assertRaises(ValueError):
            self.chunker.chunk_text("   ")

if __name__ == '__main__':
    unittest.main()
