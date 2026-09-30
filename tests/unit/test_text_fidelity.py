"""
Regression tests for M7 / M8: text cleaning must not change pronunciation or destroy
paragraph pauses, and chunking must follow sentence boundaries.
"""

import re

import pytest

from src.domain.text_chunker import SOFT_LIMIT_FACTOR, TextChunker
from src.domain.text_processor import TextProcessor


@pytest.fixture
def proc():
    return TextProcessor()


@pytest.fixture
def chunker():
    return TextChunker()


class TestApostrophes:
    @pytest.mark.parametrize("text", ["Don't", "l'amour", "п'ять", "rock'n'roll"])
    def test_in_word_apostrophes_survive(self, proc, text):
        assert proc.process_text(text) == text

    @pytest.mark.parametrize("src,expected", [("It’s", "It's"), ("м’ята", "м'ята"), ("п\u02bcять", "п'ять")])
    def test_typographic_apostrophes_are_kept_as_apostrophes(self, proc, src, expected):
        assert proc.process_text(src) == expected

    def test_reported_example(self, proc):
        assert proc.process_text("Don't l'amour п'ять") == "Don't l'amour п'ять"

    @pytest.mark.parametrize("src,expected", [
        ('"Hello"', "Hello"),
        ("'Hello'", "Hello"),
        ("“Hello” „World“ «Hi»", "Hello World Hi"),
        ("‘x’", "x"),
    ])
    def test_quotation_marks_removed(self, proc, src, expected):
        assert proc.process_text(src) == expected

    def test_quote_next_to_apostrophe(self, proc):
        assert proc.process_text("'Don't go,' she said.") == "Don't go, she said."

    def test_leading_and_trailing_apostrophe_is_not_in_word(self, proc):
        assert proc.process_text("'tis and dogs'") == "tis and dogs"

    def test_no_placeholder_leaks(self, proc):
        assert "\x00" not in proc.process_text("a'b c'd")


class TestNewlines:
    def test_single_newline_kept(self, proc):
        assert proc.process_text("Line 1\nLine 2") == "Line 1\nLine 2"

    def test_paragraph_break_kept(self, proc):
        assert proc.process_text("Para one.\n\nPara two.") == "Para one.\n\nPara two."

    def test_many_blank_lines_collapsed_to_one_paragraph_break(self, proc):
        assert proc.process_text("A.\n\n\n\n\nB.") == "A.\n\nB."

    def test_crlf_and_cr_normalised(self, proc):
        assert proc.process_text("A.\r\nB.\rC.") == "A.\nB.\nC."

    def test_trailing_spaces_before_newline_stripped(self, proc):
        assert proc.process_text("A.   \t\nB.") == "A.\nB."

    def test_no_four_space_replacement(self, proc):
        assert "    " not in proc.process_text("A.\nB.\n\nC.")

    def test_dividers_still_removed(self, proc):
        assert proc.process_text("A\n***\nB") == "A\n\nB"

    def test_none_still_rejected(self, proc):
        with pytest.raises(ValueError):
            proc.process_text(None)

    def test_paragraph_break_reaches_chunker_as_boundary(self, proc, chunker):
        text = proc.process_text("First paragraph without dot\n\nSecond paragraph without dot")
        chunks = chunker.chunk_text(text, chunk_size=40)
        assert [c.text_content.strip() for c in chunks] == [
            "First paragraph without dot",
            "Second paragraph without dot",
        ]

    def test_xtts_chunker_splits_paragraphs_from_processed_text(self, proc):
        """XttsSentenceChunker._NEWLINE_RE is live again: paragraphs survive preprocessing."""
        from src.domain.xtts_sentence_chunker import XttsSentenceChunker
        text = proc.process_text("Title without dot\n\nBody line without dot")
        segments = XttsSentenceChunker(max_chars=30).split(text)
        assert [seg.text for seg in segments] == ["Title without dot", "Body line without dot"]


def _joined(chunks):
    return "".join(c.text_content for c in chunks)


class TestSentenceChunking:
    def test_never_loses_or_reorders_text(self, chunker):
        text = "Hello there. How are you? I am fine, thanks; and you\n\nNew para here. " * 7
        for size in (10, 30, 57, 200, 1000):
            assert _joined(chunker.chunk_text(text, chunk_size=size)) == text

    def test_chunks_end_on_sentence_boundaries(self, chunker):
        text = "First sentence here. Second sentence here. Third sentence here. Fourth one."
        chunks = chunker.chunk_text(text, chunk_size=45)
        for c in chunks[:-1]:
            assert re.search(r"[.!?]\s*$", c.text_content), c.text_content

    def test_sentence_not_cut_mid_way_within_soft_limit(self, chunker):
        sentence = "This single sentence is slightly longer than the nominal limit of the chunk."
        chunks = chunker.chunk_text(sentence, chunk_size=len(sentence) - 10)
        assert len(chunks) == 1

    def test_soft_limit_is_bounded(self, chunker):
        text = "A very long sentence, " + "with many clauses, " * 40 + "that never ends."
        size = 100
        for c in chunker.chunk_text(text, chunk_size=size):
            assert len(c.text_content) <= size * SOFT_LIMIT_FACTOR + 1

    def test_long_sentence_splits_at_clause_not_word(self, chunker):
        text = "Alpha beta gamma delta, epsilon zeta eta theta, iota kappa lambda mu."
        chunks = chunker.chunk_text(text, chunk_size=25)
        assert chunks[0].text_content.rstrip().endswith(",")

    def test_unbreakable_token_is_hard_cut(self, chunker):
        chunks = chunker.chunk_text("x" * 250, chunk_size=100)
        assert _joined(chunks) == "x" * 250
        assert all(len(c.text_content) <= 100 for c in chunks)

    def test_numbering_starts_at_one_and_is_sequential(self, chunker):
        chunks = chunker.chunk_text("One. Two. Three. Four. Five.", chunk_size=8)
        assert [c.chunk_number for c in chunks] == list(range(1, len(chunks) + 1))

    def test_no_whitespace_only_chunks(self, chunker):
        chunks = chunker.chunk_text("Hello.\n\n\n   \nWorld.", chunk_size=5)
        assert all(c.text_content.strip() for c in chunks)

    def test_ellipsis_question_and_exclamation_split(self, chunker):
        chunks = chunker.chunk_text("Wait... Really? Yes! Done.", chunk_size=8)
        assert len(chunks) >= 3

    def test_abbreviation_free_decimal_not_split(self, chunker):
        text = "Pi is 3.14159 roughly."
        assert len(chunker.chunk_text(text, chunk_size=1000)) == 1

    def test_closing_quote_stays_with_sentence(self, chunker):
        chunks = chunker.chunk_text('He said "Go home." Then he left.', chunk_size=20)
        assert chunks[0].text_content.rstrip().endswith('"')

    def test_cyrillic_text(self, chunker):
        text = "Привіт, світе. Як справи? Все добре!"
        chunks = chunker.chunk_text(text, chunk_size=15)
        assert _joined(chunks) == text

    def test_default_size_keeps_sentences_whole(self, chunker):
        sentence = "Word " * 150 + "end."
        text = sentence + " " + sentence
        chunks = chunker.chunk_text(text)
        assert chunks[0].text_content.rstrip().endswith("end.")
