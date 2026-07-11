#!/usr/bin/env python3
"""
Performance benchmarking script for ReadAloud.

This script runs a series of tests to measure the time taken for text processing,
chunking, and (simulated) audio generation.
"""

import sys
import time
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import List

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domain.text_processor import TextProcessor
from src.domain.text_chunker import TextChunker
from src.infrastructure.file_manager import FileManager

# Configure logging to console
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

@dataclass
class BenchmarkResult:
    case_name: str
    text_length: int
    num_chunks: int
    processing_time: float
    chunking_time: float
    total_time: float

def run_benchmark(name: str, text: str, chunk_size: int = 180) -> BenchmarkResult:
    processor = TextProcessor()
    chunker = TextChunker()
    
    logging.info(f"Running benchmark: {name} ({len(text)} characters)")
    
    start_total = time.perf_counter()
    
    # 1. Processing
    start_proc = time.perf_counter()
    processed = processor.process_text(text)
    end_proc = time.perf_counter()
    
    # 2. Chunking
    start_chunk = time.perf_counter()
    chunks = chunker.chunk_text(processed, chunk_size=chunk_size)
    end_chunk = time.perf_counter()
    
    end_total = time.perf_counter()
    
    result = BenchmarkResult(
        case_name=name,
        text_length=len(text),
        num_chunks=len(chunks),
        processing_time=end_proc - start_proc,
        chunking_time=end_chunk - start_chunk,
        total_time=end_total - start_total
    )
    
    return result

def main():
    print("=" * 60)
    print("READALOUD PERFORMANCE BENCHMARK")
    print("=" * 60)
    
    # Test cases
    cases = [
        ("Small File", "Hello world. " * 100, 180),
        ("Medium File", "This is a sentence. " * 1000, 180),
        ("Large File", "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 5000, 180),
        ("Very Large File", "Huge text block for testing scalability. " * 50000, 180)
    ]
    
    results: List[BenchmarkResult] = []
    
    for name, text, size in cases:
        try:
            res = run_benchmark(name, text, size)
            results.append(res)
            print(f"{res.case_name}: {res.num_chunks} chunks, {res.total_time:.4f}s total")
            print(f"  - Processing: {res.processing_time:.4f}s")
            print(f"  - Chunking:   {res.chunking_time:.4f}s")
        except Exception as e:
            print(f"Error running {name}: {e}")
            
    print("\n" + "=" * 60)
    print(f"{'Case':<20} | {'Chunks':<7} | {'Total Time':<12}")
    print("-" * 60)
    for res in results:
        print(f"{res.case_name:<20} | {res.num_chunks:<7} | {res.total_time:.4f}s")
    print("=" * 60)

if __name__ == "__main__":
    main()
