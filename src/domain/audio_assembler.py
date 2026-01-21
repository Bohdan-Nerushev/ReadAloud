"""
Audio assembly service for combining multiple audio chunks.

This module handles concatenation of individual audio chunks into a final MP3 file.
"""

from typing import List
from pathlib import Path
import subprocess
import atexit
import signal
import logging
from typing import Set

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
        pass
    
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
        
        Args:
            audio_files: List of paths to audio files to concatenate
            output_path: Path where the final audio file will be saved
            speed: Playback speed multiplier (default 1.0)
            callback: Progress callback function
            copy_codec: If True, uses -c copy for fast concatenation (ignores speed)
        """
        if not audio_files:
            raise ValueError(
                "Audio files list cannot be empty"
            )
        
        # Verify input files exist
        for audio_file in audio_files:
            if not Path(audio_file).exists():
                raise ValueError(
                    f"Audio file does not exist: {audio_file}"
                )
        
        output_file_path = Path(output_path)
        output_dir = output_file_path.parent
        if not output_dir.exists():
            raise ValueError(
                f"Output directory does not exist: {output_dir}"
            )
            
        # Create a temporary file list for ffmpeg
        list_path = output_dir / "concat_list.txt"
        
        try:
            # 1. Calculate total duration
            total_duration = 0.0
            for path in audio_files:
                try:
                    total_duration += self._get_file_duration(path)
                except Exception:
                    pass  # skip duration check if fails

            # 2. Create file list
            with open(list_path, 'w', encoding='utf-8') as f:
                for path in audio_files:
                    safe_path = Path(path).resolve().as_posix()
                    safe_path = safe_path.replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")
            
            # 3. Construct ffmpeg command
            cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(list_path)
            ]
            
            # Apply codec/filter options
            if copy_codec:
                cmd.extend(['-c', 'copy'])
            else:
                # Apply speed filter if not 1.0
                if abs(speed - 1.0) > 0.01:
                    cmd.extend(['-filter:a', f'atempo={speed}'])
                
            cmd.extend([
                '-vn',
                '-y',
                str(output_file_path)
            ])
            
            # Run ffmpeg with Popen to capture output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True
            )
            _ACTIVE_PROCESSES.add(process)

            import re
            import time

            start_time = time.time()
            time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d+)")

            try:
                # Read stderr line by line
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
                            percentage = min(100, (current_seconds / expected_output_duration) * 100) if expected_output_duration > 0 else 0
                            
                            elapsed = time.time() - start_time
                            if percentage > 0:
                                total_estimated = elapsed * (100 / percentage)
                                remaining = total_estimated - elapsed
                                callback(percentage, remaining)

                if process.returncode != 0:
                    raise Exception(f"ffmpeg exited with code {process.returncode}")
            
            finally:
                _ACTIVE_PROCESSES.discard(process)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
            
        except subprocess.CalledProcessError as e:
            raise Exception(
                f"ffmpeg failed with error: {e.stderr}"
            ) from e
        except Exception as e:
            raise Exception(
                f"Failed to assemble audio files: {str(e)}"
            ) from e
        finally:
            # Cleanup temporary list file
            if list_path.exists():
                try:
                    list_path.unlink()
                except Exception:
                    pass

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

