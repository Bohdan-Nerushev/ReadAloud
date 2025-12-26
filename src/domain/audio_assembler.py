"""
Audio assembly service for combining multiple audio chunks.

This module handles concatenation of individual audio chunks into a final MP3 file.
"""

from typing import List
from pathlib import Path
from pydub import AudioSegment


class AudioAssembler:
    """
    Service responsible for assembling multiple audio chunks into a single audio file.
    
    This service uses pydub to concatenate audio files in sequential order.
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
        Concatenates multiple audio files into a single MP3 file.
        
        Audio files are concatenated in the order they appear in the list.
        The output file is saved to the specified path.
        
        Args:
            audio_files: List of paths to audio files to concatenate
            output_path: Path where the final audio file will be saved
            
        Raises:
            ValueError: If audio_files is empty or any file doesn't exist
            Exception: If pydub fails to process the audio
        """
        if not audio_files:
            raise ValueError(
                "Audio files list cannot be empty"
            )
        
        for audio_file in audio_files:
            file_path = Path(audio_file)
            if not file_path.exists():
                raise ValueError(
                    f"Audio file does not exist: {audio_file}"
                )
        
        output_file_path = Path(output_path)
        output_dir = output_file_path.parent
        if not output_dir.exists():
            raise ValueError(
                f"Output directory does not exist: {output_dir}"
            )
        
        try:
            combined = AudioSegment.empty()
            
            for audio_file in audio_files:
                audio_segment = AudioSegment.from_mp3(
                    audio_file
                )
                combined += audio_segment
            
            combined.export(
                str(output_file_path),
                format="mp3"
            )
            
        except Exception as e:
            raise Exception(
                f"Failed to assemble audio files: {str(e)}"
            ) from e
