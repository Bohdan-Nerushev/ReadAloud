"""
Audio generation service using Google Text-to-Speech (gTTS).

This module handles conversion of text chunks to audio files.
"""

from pathlib import Path
from gtts import gTTS
from src.domain.models import AudioChunk


class AudioGenerator:
    """
    Service responsible for generating audio files from text using gTTS.
    
    This service integrates with Google's Text-to-Speech API to convert text chunks
    into MP3 audio files.
    """
    
    def __init__(
            self
    ) -> None:
        """Initialize the AudioGenerator."""
        pass
    
    def generate_audio(
            self,
            chunk: AudioChunk,
            language: str,
            output_dir: str
    ) -> str:
        """
        Generates an audio file from a text chunk using gTTS.
        
        The audio file is saved as {chunk_number}.mp3 in the specified output directory.
        
        Args:
            chunk: The AudioChunk containing text to convert
            language: Language code for speech synthesis (e.g., 'en', 'uk', 'de', 'ru')
            output_dir: Directory where the audio file will be saved
            
        Returns:
            Absolute path to the generated audio file
            
        Raises:
            ValueError: If chunk is None or output_dir doesn't exist
            Exception: If gTTS fails to generate audio
        """
        if chunk is None:
            raise ValueError(
                "Chunk cannot be None"
            )
        
        output_path = Path(output_dir)
        if not output_path.exists():
            raise ValueError(
                f"Output directory does not exist: {output_dir}"
            )
        
        if not output_path.is_dir():
            raise ValueError(
                f"Output path is not a directory: {output_dir}"
            )
        
        audio_filename = f"{chunk.chunk_number}.mp3"
        audio_file_path = output_path / audio_filename
        
        try:
            tts = gTTS(
                text=chunk.text_content,
                lang=language,
                slow=False
            )
            
            tts.save(
                str(audio_file_path)
            )
            
        except Exception as e:
            raise Exception(
                f"Failed to generate audio for chunk {chunk.chunk_number}: {str(e)}"
            ) from e
        
        return str(audio_file_path.absolute())
