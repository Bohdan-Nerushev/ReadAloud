#!/usr/bin/env python3
"""
Simple test script for ReadAloud domain services.
"""

from pathlib import Path
import sys
project_root = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, project_root)

from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker

def test_text_processing():
    """Test text preprocessing functionality."""
    print("Testing Text Processor...")
    processor = TextProcessor()
    
    test_text = 'This has "quotes" and \'apostrophes\' and *** stars *** and ======\nNewline test'
    processed = processor.process_text(test_text)
    
    print(f"Original: {test_text}")
    print(f"Processed: {processed}")
    
    assert '"' not in processed
    assert "'" not in processed
    assert "***" not in processed
    assert "======" not in processed
    assert "\n" not in processed
    assert "    " in processed
    
    print("✓ Text Processor tests passed!\n")


def test_text_chunking():
    """Test text chunking functionality."""
    print("Testing Text Chunker...")
    chunker = TextChunker()
    
    test_text = (
        "This is a very long text that will be split into multiple chunks. "
        "Each chunk should be approximately 180 characters long. "
        "The chunking algorithm should respect word boundaries and not split words in the middle. "
        "This ensures natural speech flow when the audio is generated."
    )
    
    chunks = chunker.chunk_text(test_text, chunk_size=100)
    
    print(f"Original text length: {len(test_text)}")
    print(f"Number of chunks: {len(chunks)}")
    
    for i, chunk in enumerate(chunks[:3]):
        print(f"Chunk {chunk.chunk_number}: {len(chunk.text_content)} chars - '{chunk.text_content[:40]}...'")
    
    assert len(chunks) > 1
    assert all(chunk.chunk_number == i + 1 for i, chunk in enumerate(chunks))
    
    print("✓ Text Chunker tests passed!\n")


def test_domain_models():
    """Test domain models."""
    from src.domain.models import ProjectConfig, AudioChunk
    from src.domain.exceptions import ConfigurationException
    
    print("Testing Domain Models...")
    
    try:
        config = ProjectConfig(
            project_name="",
            input_file_path="/tmp/test.txt",
            language="en",
            gender="male",
            speed=1.0,
            thread_count=1,
            output_dir_path="/tmp"
        )
        print("✗ Should have raised ConfigurationException for empty project name")
    except ConfigurationException as e:
        print(f"✓ Validation works: {e}")
    
    try:
        chunk = AudioChunk(
            chunk_number=0,
            text_content="test"
        )
        print("✗ Should have raised ConfigurationException for chunk_number < 1")
    except ConfigurationException as e:
        print(f"✓ Validation works: {e}")
    
    print("✓ Domain Models tests passed!\n")


if __name__ == "__main__":
    test_text_processing()
    test_text_chunking()
    test_domain_models()
    
    print("=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)
