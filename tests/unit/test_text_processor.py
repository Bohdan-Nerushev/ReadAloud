import unittest
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.domain.text_processor import TextProcessor

class TestTextProcessor(unittest.TestCase):
    def setUp(self):
        self.processor = TextProcessor()

    def test_process_text_removes_quotes(self):
        """test_process_text_removes_quotes: Verifies that quotation marks (not in-word apostrophes) are removed."""
        input_text = 'Text with "double" and \'single\' quotes.'
        expected = "Text with double and single quotes."
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_removes_special_markers(self):
        """test_process_text_removes_special_markers: Verifies removal of special markers like *** and ======"""
        input_text = "Text with *** markers and ====== equals."
        expected = "Text with  markers and  equals."
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_replaces_newlines(self):
        """test_process_text_replaces_newlines: Verifies that line breaks are preserved (they carry pauses)."""
        input_text = "Line 1\nLine 2"
        expected = "Line 1\nLine 2"
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_empty_input(self):
        """test_process_text_empty_input: Verifies behavior when the input string is empty."""
        input_text = ""
        expected = ""
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_none_input(self):
        """test_process_text_none_input: Verifies ValueError is raised if the input text is None."""
        with self.assertRaises(ValueError) as cm:
            self.processor.process_text(None)
        self.assertEqual(str(cm.exception), "Input text cannot be None")

    def test_process_text_complex_cleaning(self):
        """test_process_text_complex_cleaning: Combined multi-stage text cleaning."""
        input_text = '***Header***\n"Content" with \'quotes\' and ======'
        expected = "Header\nContent with quotes and "
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_removes_decorative_dividers(self):
        """test_process_text_removes_decorative_dividers: Verifies removal of decorative dividers like ___, ---, ###."""
        input_text = "Chapter 1\n___\nText with --- dividers and ### headers."
        expected = "Chapter 1\n\nText with  dividers and  headers."
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_normalizes_ellipses_and_dots(self):
        """Verifies that consecutive dots and ellipses (e.g. …., ….., ..) collapse to '...'."""
        input_text = "Взошла луна…. Потом появился густой туман."
        expected = "Взошла луна... Потом появился густой туман."
        self.assertEqual(self.processor.process_text(input_text), expected)

        input_text_dots = "Точка..... Следующая"
        expected_dots = "Точка... Следующая"
        self.assertEqual(self.processor.process_text(input_text_dots), expected_dots)

    def test_process_text_cleans_dialogue_dash_with_ellipsis(self):
        """Verifies that dialogue dashes followed by ellipses (-…, —...) are cleaned to '— '."""
        input_text = "Задумка:\n\n-… После того, как я возьму интервью"
        expected = "Задумка:\n\n— После того, как я возьму интервью"
        self.assertEqual(self.processor.process_text(input_text), expected)

        input_inline = "Сказал он: — ... Привет!"
        expected_inline = "Сказал он: — Привет!"
        self.assertEqual(self.processor.process_text(input_inline), expected_inline)

    def test_process_text_drops_lines_without_alphanumeric_characters(self):
        """Verifies that lines without any speakable characters (dashes, symbols, dots) are dropped."""
        input_text = "Глава 1\n\n— — —\n\nТекст главы."
        expected = "Глава 1\n\nТекст главы."
        self.assertEqual(self.processor.process_text(input_text), expected)

        input_dots = "Раздел 1\n\n...\n\nРаздел 2"
        expected_dots = "Раздел 1\n\nРаздел 2"
        self.assertEqual(self.processor.process_text(input_dots), expected_dots)

    def test_process_text_strips_leading_punctuation_on_lines(self):
        """Verifies that punctuation marks at the start of a line or after line breaks are stripped."""
        input_text = "Глава 1\n\n. С незаинтересованным видом\n\n! На это как будто\n\n... И они пошли"
        expected = "Глава 1\n\nС незаинтересованным видом\n\nНа это как будто\n\nИ они пошли"
        self.assertEqual(self.processor.process_text(input_text), expected)


if __name__ == '__main__':
    unittest.main()

