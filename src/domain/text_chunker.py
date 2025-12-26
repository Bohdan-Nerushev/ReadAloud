"""
Text chunking service.

This module handles splitting processed text into appropriately sized chunks for audio generation.
"""

from typing import List
from src.domain.models import AudioChunk


class TextChunker:
    """
    Service responsible for splitting text into chunks suitable for text-to-speech conversion.
    
    Chunks are created with a target size, but word boundaries are respected to avoid splitting
    words in the middle.
    """
    
    def __init__(
            self
    ) -> None:
        """Initialize the TextChunker."""
        pass
    
    def chunk_text(
            self,
            text: str,
            chunk_size: int = 180
    ) -> List[AudioChunk]:
        """
        Splits text into chunks of approximately chunk_size characters.
        
        If the chunk_size-th character falls in the middle of a word, the entire word
        is included in the chunk, plus one additional space. This ensures that words
        are not split across chunks.
        
        Args:
            text: The text to split into chunks
            chunk_size: Target size for each chunk in characters
            
        Returns:
            List of AudioChunk objects numbered sequentially starting from 1
            
        Raises:
            ValueError: If text is empty or chunk_size is not positive
        """
        if not text or not text.strip():
            raise ValueError(
                "Text cannot be empty"
            )
        
        if chunk_size <= 0:
            raise ValueError(
                f"Chunk size must be positive, got: {chunk_size}"
            )
        
        chunks: List[AudioChunk] = []
        current_position = 0
        chunk_number = 1
        
        while current_position < len(text):
            end_position = min(
                current_position + chunk_size,
                len(text)
            )
            
            if end_position < len(text):
                while end_position < len(text) and text[end_position] not in ' \t':
                    end_position += 1
                
                if end_position < len(text):
                    end_position += 1
            
            chunk_text = text[current_position:end_position]
            
            chunk = AudioChunk(
                chunk_number=chunk_number,
                text_content=chunk_text
            )
            chunks.append(chunk)
            
            current_position = end_position
            chunk_number += 1
        
        return chunks
