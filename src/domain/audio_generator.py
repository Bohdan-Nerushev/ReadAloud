"""
Audio generation service using Google Text-to-Speech (gTTS).

This module handles conversion of text chunks to audio files.
"""

import asyncio
from pathlib import Path
import edge_tts
from src.domain.models import AudioChunk


class AudioGenerator:
    """
    Service responsible for generating audio files from text using Edge TTS.
    
    This service integrates with Microsoft Edge's Text-to-Speech API to convert text chunks
    into MP3 audio files. It offers better quality and higher rate limits than gTTS.
    """
    
    # Mapping of language codes and genders to Edge TTS voices
    VOICE_MAPPING = {
        'en': {
            'male': 'en-US-GuyNeural',
            'female': 'en-US-AriaNeural'
        },
        'uk': {
            'male': 'uk-UA-OstapNeural',
            'female': 'uk-UA-PolinaNeural'
        },
        'de': {
            'male': 'de-DE-ConradNeural',
            'female': 'de-DE-KatjaNeural'
        },
        'ru': {
            'male': 'ru-RU-DmitryNeural',
            'female': 'ru-RU-SvetlanaNeural'
        }
    }
    
    def __init__(
            self
    ) -> None:
        """Initialize the AudioGenerator."""
        pass
    
    def generate_audio(
            self,
            chunk: AudioChunk,
            language: str,
            gender: str,
            output_dir: str
    ) -> str:
        """
        Generates an audio file from a text chunk using Edge TTS.
        
        The audio file is saved as {chunk_number}.mp3 in the specified output directory.
        
        Args:
            chunk: The AudioChunk containing text to convert
            language: Language code for speech synthesis
            gender: Gender of the voice ('male' or 'female')
            output_dir: Directory where the audio file will be saved
            
        Returns:
            Absolute path to the generated audio file
            
        Raises:
            ValueError: If chunk is None or output_dir doesn't exist
            Exception: If generation fails
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
        
        voice = self.VOICE_MAPPING.get(language, {}).get(gender, 'en-US-GuyNeural')
        
        async def _generate() -> None:
            communicate = edge_tts.Communicate(chunk.text_content, voice)
            await communicate.save(str(audio_file_path))
            
        try:
            # Run the async generation in a new event loop
            asyncio.run(_generate())
            
        except Exception as e:
            raise Exception(
                f"Failed to generate audio for chunk {chunk.chunk_number}: {str(e)}"
            ) from e
        
        return str(audio_file_path.absolute())
