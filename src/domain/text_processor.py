"""
Text preprocessing service.

This module handles cleaning and sanitizing text before chunking and audio generation.
"""

import re
from typing import Optional


class TextProcessor:
    """
    Service responsible for preprocessing text to remove or replace potentially problematic characters.
    
    This ensures that the text is safe for processing by the TTS engine and prevents issues with special characters
    that might cause errors in text-to-speech conversion.
    """
    
    def __init__(
            self
    ) -> None:
        """Initialize the TextProcessor."""
        # Decorative dividers (___, ***, ===, ---, ###, em/en dashes, and spaced variants like * * *) carry no speech.
        self._divider_pattern = re.compile(
            r'_{2,}|\*{2,}|={2,}|-{3,}|#{2,}|[\u2014\u2013]{2,}|(?:\*\s*){2,}\*|(?:\u2014\s*){2,}\u2014|(?:-\s*){2,}-'
        )
        # An apostrophe between two letters is part of the word (don't, l'amour, п'ять).
        self._word_apostrophe = re.compile(
            r"(?<=[^\W\d_])['\u2019\u02bc\u2018](?=[^\W\d_])"
        )
        # Every other quotation mark is decoration and is dropped.
        self._quote_pattern = re.compile(r'["\u201c\u201d\u201e\u00ab\u00bb\u2018\u2019\']')
        self._many_newlines = re.compile(r'\n{3,}')
        self._line_edge_spaces = re.compile(r'[ \t]+(?=\n)')
        # Repeated dots normalized to standard '...' (4 or more dots)
        self._dots_pattern = re.compile(r'\.{4,}')
        # Dialogue dash combined with dots/ellipses at start of line or before words (e.g. '-… ' or '—... ') -> '— '
        self._dash_dots_line = re.compile(r'(?m)^[ \t]*[-—–]\s*\.+\s*')
        self._dash_dots_word = re.compile(r'[-—–]\s*\.+\s*(?=[^\W\d_])')
        # Punctuation at start of line or after line breaks
        self._leading_punctuation = re.compile(r'(?m)^[ \t]*[.!?,;:…]+[ \t]*')

    def process_text(
            self,
            raw_text: str
    ) -> str:
        """
        Processes raw text by removing or replacing dangerous characters.
        
        Transformations applied:
        - Quotation marks (" “ ” „ « » and single quotes) are removed, but an
          apostrophe inside a word is kept (don't, l'amour, п'ять) because
          removing it changes the pronunciation
        - Decorative dividers (***, ===, ---, ___, ###, em-dashes, spaced dividers) are removed
        - Repeated dots/ellipses (...., …., etc.) are normalised to '...'
        - Dialogue dashes combined with ellipses (-…, —...) are cleaned to dialogue dashes
        - Lines without speakable characters are dropped
        - Line breaks are preserved as paragraph/pause information
          (CRLF normalised, runs of 3+ newlines collapsed to a blank line)

        Args:
            raw_text: The raw input text to process
            
        Returns:
            Cleaned and processed text
            
        Raises:
            ValueError: If raw_text is None
        """
        if raw_text is None:
            raise ValueError(
                "Input text cannot be None"
            )
        
        text = raw_text.replace('\r\n', '\n').replace('\r', '\n')

        # Protect in-word apostrophes, drop other quotes, then restore them.
        marker = '\x00'
        text = self._word_apostrophe.sub(marker, text)
        text = self._quote_pattern.sub('', text)
        text = text.replace(marker, "'")

        # Normalize unicode ellipsis and Chinese punctuation to standard ASCII punctuation
        text = text.replace('\u2026', '...').replace('。', '.').replace('！', '!').replace('？', '?')

        text = self._divider_pattern.sub('', text)
        text = self._dots_pattern.sub('...', text)
        text = self._dash_dots_line.sub('— ', text)
        text = self._dash_dots_word.sub('— ', text)
        text = self._leading_punctuation.sub('', text)
        text = self._line_edge_spaces.sub('', text)

        # Drop lines that contain only non-speech/decorative characters (no alphanumeric chars)
        lines = [
            line if (not line.strip() or any(c.isalnum() for c in line)) else ''
            for line in text.split('\n')
        ]
        text = '\n'.join(lines)
        return self._many_newlines.sub('\n\n', text)
