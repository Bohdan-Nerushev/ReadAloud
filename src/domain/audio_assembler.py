"""
Audio assembly service for combining multiple audio chunks.

This module handles concatenation of individual audio chunks into a final MP3 file.
"""

from typing import List
from pathlib import Path
import subprocess


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
            output_path: str
    ) -> None:
        """
        Concatenates multiple audio files into a single MP3 file using ffmpeg.
        
        Audio files are concatenated in the order they appear in the list.
        The output file is saved to the specified path.
        
        Args:
            audio_files: List of paths to audio files to concatenate
            output_path: Path where the final audio file will be saved
            
        Raises:
            ValueError: If audio_files is empty or any file doesn't exist
            Exception: If ffmpeg fails to process the audio
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
            # 1. Create file list
            # ffmpeg concat demuxer format: file '/path/to/file'
            with open(list_path, 'w', encoding='utf-8') as f:
                for path in audio_files:
                    # Resolve to absolute path and use forward slashes
                    safe_path = Path(path).resolve().as_posix()
                    # Escape single quotes in path if present
                    safe_path = safe_path.replace("'", "'\\''")
                    f.write(f"file '{safe_path}'\n")
            
            # 2. Construct ffmpeg command
            # -f concat: use concat demuxer
            # -safe 0: allow unsafe paths (files not in current dir)
            # -i list_path: input file list
            # -filter:a "atempo=1.25": increase speed by 1.25x
            # -vn: disable video (audio only)
            # -y: overwrite output
            # -b:a 192k: set audio bitrate (optional, good for quality)
            cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(list_path),
                '-filter:a', 'atempo=1.25',
                '-vn',
                '-y',
                str(output_file_path)
            ]
            
            # Run ffmpeg
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
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
