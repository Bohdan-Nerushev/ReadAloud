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
        """test_process_text_removes_quotes: Перевіряє, що одинарні (') та подвійні (") лапки видаляються."""
        input_text = 'Text with "double" and \'single\' quotes.'
        expected = "Text with double and single quotes."
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_removes_special_markers(self):
        """test_process_text_removes_special_markers: Перевіряє видалення маркерів *** та ======"""
        input_text = "Text with *** markers and ====== equals."
        expected = "Text with  markers and  equals."
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_replaces_newlines(self):
        """test_process_text_replaces_newlines: Перевіряє, що символи \n замінюються на 4 пробіли."""
        input_text = "Line 1\nLine 2"
        expected = "Line 1    Line 2"
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_empty_input(self):
        """test_process_text_empty_input: Перевіряє поведінку при порожньому рядку."""
        input_text = ""
        expected = ""
        self.assertEqual(self.processor.process_text(input_text), expected)

    def test_process_text_none_input(self):
        """test_process_text_none_input: Перевіряє викидання ValueError, якщо вхідний текст None."""
        with self.assertRaises(ValueError) as cm:
            self.processor.process_text(None)
        self.assertEqual(str(cm.exception), "Input text cannot be None")

    def test_process_text_complex_cleaning(self):
        """test_process_text_complex_cleaning: Комбінована очистка."""
        input_text = '***Header***\n"Content" with \'quotes\' and ======'
        expected = "Header    Content with quotes and "
        self.assertEqual(self.processor.process_text(input_text), expected)

if __name__ == '__main__':
    unittest.main()
