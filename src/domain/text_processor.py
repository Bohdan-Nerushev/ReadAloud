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
        # Compile regex patterns for performance
        # Pattern to remove ", ', ***, and ======
        self._sanitization_pattern = re.compile(
            r'["\']|\*\*\*|======'
        )
        # Pattern to replace newlines with four spaces
        self._newline_pattern = re.compile(
            r'\n'
        )
    
    def process_text(
            self,
            raw_text: str
    ) -> str:
        """
        Processes raw text by removing or replacing dangerous characters.
        
        Transformations applied:
        - Double quotes (") replaced with empty string
        - Single quotes (') replaced with empty string
        - Three consecutive asterisks (***) replaced with empty string
        - Six consecutive equals signs (======) replaced with empty string
        - Newline characters replaced with four spaces
        
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
        
        # Single-pass sanitization using compiled regex
        processed = self._sanitization_pattern.sub(
            '',
            raw_text
        )
        
        # Replace newlines
        processed = self._newline_pattern.sub(
            '    ',
            processed
        )
        
        return processed
