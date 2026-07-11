#!/usr/bin/env python3
import sys
import re
import argparse
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

@dataclass(frozen=True)
class GenerationEvent:
    """Represents a chunk generation event from the log file."""
    timestamp: datetime
    content_duration: float

class LogParser:
    """Handles parsing of the log file and filtering entries."""
    
    # Example: 2026-04-03 20:33:06,025 [bad1d214...] [DEBUG] root: Chunk 1616 generated: ... (24.696s)
    ENTRY_PATTERN = re.compile(
        r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) .* Chunk \d+ generated: .* \(([\d\.]+)s\)'
    )

    def __init__(self, log_path: Path):
        self._log_path = log_path

    def get_events(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> List[GenerationEvent]:
        """Parses the log file and returns events within the time range."""
        events = []
        if not self._log_path.exists():
            return events

        with open(self._log_path, 'r', encoding='utf-8') as f:
            for line in f:
                match = self.ENTRY_PATTERN.search(line)
                if not match:
                    continue

                ts_str, duration_str = match.groups()
                # Replacing comma with dot for strptime to handle milliseconds if needed, 
                # but %f handles 3 or 6 digits. strptime expects dot usually if we treat it as float, 
                # but here it's literally part of the format.
                try:
                    ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S,%f')
                except ValueError:
                    # Fallback for formats without ms if any
                    ts = datetime.strptime(ts_str.split(',')[0], '%Y-%m-%d %H:%M:%S')
                
                if start_time and ts < start_time:
                    continue
                if end_time and ts > end_time:
                    continue

                events.append(GenerationEvent(
                    timestamp=ts,
                    content_duration=float(duration_str)
                ))
        return events

class HealthReporter:
    """Analyzes errors in logs (Optional enhancement)."""
    ERROR_PATTERN = re.compile(r'\[ERROR\]')

    def __init__(self, log_path: Path):
        self._log_path = log_path

    def count_errors(self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None) -> int:
        count = 0
        # Simple line-by-line check for errors in range
        return count # Implementation deferred if not critical

class SpeedCalculator:
    """Calculates various metrics from generation events."""
    
    def __init__(self, events: List[GenerationEvent]):
        self._events = events

    def calculate_stats(self) -> Optional[Dict[str, Any]]:
        """Computes summary statistics from the collected events."""
        if not self._events:
            return None

        # Sort events by time to find real duration
        sorted_events = sorted(self._events, key=lambda x: x.timestamp)
        first_event = sorted_events[0]
        last_event = sorted_events[-1]
        
        total_content_seconds = sum(e.content_duration for e in self._events)
        chunk_counts = len(self._events)
        
        real_seconds = (last_event.timestamp - first_event.timestamp).total_seconds()
        
        # Avoid division by zero
        real_speed = total_content_seconds / real_seconds if real_seconds > 0 else 0
        avg_chunk_len = total_content_seconds / chunk_counts if chunk_counts > 0 else 0
        
        return {
            "start": first_event.timestamp,
            "end": last_event.timestamp,
            "chunks": chunk_counts,
            "content_total_s": total_content_seconds,
            "real_elapsed_s": real_seconds,
            "speed_multiplier": real_speed,
            "avg_chunk_s": avg_chunk_len
        }

class CliInterface:
    """Handles command line arguments and output formatting."""

    @staticmethod
    def parse_args():
        parser = argparse.ArgumentParser(description="Analyze ReadAloud log generation speed.")
        default_log_path = str(Path(__file__).resolve().parent.parent / "logs" / "app.log")
        parser.add_argument("--log", type=str, default=default_log_path,
                            help="Path to the log file")
        parser.add_argument("--start", type=str, help="Start time (YYYY-MM-DD HH:MM:SS)")
        parser.add_argument("--end", type=str, help="End time (YYYY-MM-DD HH:MM:SS)")
        return parser.parse_args()

    @staticmethod
    def display_results(stats: Dict[str, Any]):
        print("\n=== ReadAloud Work Speed Analysis ===")
        print(f"Period:       {stats['start']} --> {stats['end']}")
        print(f"Duration:     {stats['real_elapsed_s']:.2f} seconds")
        print("-" * 37)
        print(f"Chunks processed:      {stats['chunks']}")
        print(f"Total audio produced:  {stats['content_total_s']:.2f}s "
              f"({stats['content_total_s']/60:.2f} min)")
        print(f"Avg chunk length:      {stats['avg_chunk_s']:.2f}s")
        print("-" * 37)
        print(f"AVERAGE WORK SPEED:    {stats['speed_multiplier']:.2f}x")
        print("(Ratio of audio duration to real processing time)")
        print("=====================================\n")

def main():
    args = CliInterface.parse_args()
    
    try:
        start_ts = datetime.strptime(args.start, '%Y-%m-%d %H:%M:%S') if args.start else None
        end_ts = datetime.strptime(args.end, '%Y-%m-%d %H:%M:%S') if args.end else None
    except ValueError as e:
        print(f"Error parsing date: {e}")
        print("Expected format: YYYY-MM-DD HH:MM:SS")
        sys.exit(1)

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"File not found: {log_path}")
        sys.exit(1)

    parser = LogParser(log_path)
    events = parser.get_events(start_ts, end_ts)
    
    if not events:
        print("No matching log entries found in the specified time range.")
        return

    calculator = SpeedCalculator(events)
    stats = calculator.calculate_stats()
    
    if stats:
        CliInterface.display_results(stats)

if __name__ == "__main__":
    main()
