"""
Audio assembly service for combining multiple audio chunks.

This module handles concatenation of individual audio chunks into a final MP3 file.
"""

import subprocess
import atexit
import signal
import logging
import uuid
import threading
from pathlib import Path
from typing import Set, List



# Global registry for active subprocesses
_ACTIVE_PROCESSES: Set[subprocess.Popen] = set()

def _cleanup_processes():
    """Kills all active subprocesses on exit."""
    if _ACTIVE_PROCESSES:
        logging.info(f"Cleaning up {len(_ACTIVE_PROCESSES)} active subprocesses...")
        for p in list(_ACTIVE_PROCESSES):
            try:
                p.kill()
            except Exception:
                pass

atexit.register(_cleanup_processes)


class AudioAssembler:
    """
    Service responsible for assembling multiple audio chunks into a single audio file.
    
    This service uses ffmpeg to concatenate audio files in sequential order.
    """
    
    def __init__(
            self
    ) -> None:
        """Initialize the AudioAssembler."""
        self._active_processes: Set[subprocess.Popen] = set()
        self._lock = threading.Lock()
    
    def assemble_audio(
            self,
            audio_files: List[str],
            output_path: str,
            speed: float = 1.0,
            callback: callable = None,
            copy_codec: bool = False
    ) -> None:
        """
        Concatenates multiple audio files into a single MP3 file using ffmpeg.
        """
        if not audio_files:
            raise ValueError("Audio files list cannot be empty")
        
        output_file_path = Path(output_path)
        output_dir = output_file_path.parent
            
        # Create a temporary file list for ffmpeg
        list_path = output_dir / f"concat_{uuid.uuid4().hex}.txt"
        
        try:
            # 1. Calculate total duration
            total_duration = self._calculate_total_duration(audio_files)

            # 2. Create file list
            self._create_concat_list(audio_files, list_path)
            
            # 3. Construct ffmpeg command
            cmd = self._build_ffmpeg_command(list_path, output_file_path, speed, copy_codec)
            
            # Run ffmpeg
            self._execute_ffmpeg(cmd, total_duration, speed, callback)
            
        except Exception as e:
            logging.error(f"Failed to assemble audio files: {e}")
            raise Exception(f"Failed to assemble audio files: {str(e)}") from e
        finally:
            if list_path.exists():
                try:
                    list_path.unlink()
                except Exception:
                    pass

    def _calculate_total_duration(self, audio_files: List[str]) -> float:
        total = 0.0
        for path in audio_files:
            try:
                total += self._get_file_duration(path)
            except Exception:
                pass
        return total

    def _create_concat_list(self, audio_files: List[str], list_path: Path) -> None:
        with open(list_path, 'w', encoding='utf-8') as f:
            for path in audio_files:
                safe_path = Path(path).resolve().as_posix()
                safe_path = safe_path.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

    def _build_ffmpeg_command(
        self, 
        list_path: Path, 
        output_file_path: Path, 
        speed: float, 
        copy_codec: bool
    ) -> List[str]:
        cmd = [
            'ffmpeg', '-f', 'concat', '-safe', '0', '-i', str(list_path)
        ]
        if copy_codec:
            cmd.extend(['-c', 'copy'])
        else:
            if abs(speed - 1.0) > 0.01:
                cmd.extend(['-filter:a', f'atempo={speed}'])
        cmd.extend(['-vn', '-y', str(output_file_path)])
        return cmd

    def _execute_ffmpeg(
        self, 
        cmd: List[str], 
        total_duration: float, 
        speed: float, 
        callback: callable
    ) -> None:
        process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        with self._lock:
            self._active_processes.add(process)
            _ACTIVE_PROCESSES.add(process) # Keep global for atexit fallback

        try:
            self._monitor_process(process, total_duration, speed, callback)
            if process.returncode != 0:
                raise Exception(f"ffmpeg exited with code {process.returncode}")
        finally:
            with self._lock:
                self._active_processes.discard(process)
                _ACTIVE_PROCESSES.discard(process)
            if process.poll() is None:
                process.terminate()

    def _monitor_process(
        self, 
        process: subprocess.Popen, 
        total_duration: float, 
        speed: float, 
        callback: callable
    ) -> None:
        import re
        import time
        start_time = time.time()
        time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")

        while True:
            line = process.stderr.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue
            
            if callback and total_duration > 0:
                match = time_pattern.search(line)
                if match:
                    hours, minutes, seconds = map(float, match.groups())
                    current_seconds = hours * 3600 + minutes * 60 + seconds
                    expected_output_duration = total_duration / speed
                    percentage = min(100.0, (current_seconds / expected_output_duration) * 100.0)
                    
                    elapsed = time.time() - start_time
                    if percentage > 0:
                        total_estimated = elapsed * (100.0 / percentage)
                        callback(percentage, total_estimated - elapsed)

    def stop(self) -> None:
        """Stops all active ffmpeg processes managed by this instance."""
        with self._lock:
            for p in list(self._active_processes):
                try:
                    p.terminate()
                except Exception:
                    pass
            self._active_processes.clear()

    def _get_file_duration(self, file_path: str) -> float:

        """Get duration of an audio file in seconds using ffprobe."""
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            str(file_path)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())

