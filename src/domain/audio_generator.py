"""
Audio generation service using Edge TTS.

This module handles conversion of text chunks to audio files.
"""

import asyncio
import logging
import threading
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
        # Thread-local storage for event loops
        self._thread_local = threading.local()
    
    def _get_or_create_loop(self) -> asyncio.AbstractEventLoop:
        """
        Gets or creates an event loop for the current thread.
        
        Returns:
            The event loop for the current thread.
        """
        if not hasattr(self._thread_local, 'loop'):
            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._thread_local.loop = loop
        
        return self._thread_local.loop

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
            ValueError: If chunk/parameters are invalid or voice is not found
            Exception: If generation fails
        """
        if chunk is None:
            raise ValueError("Chunk cannot be None")
        
        # Voice Validation
        if language not in self.VOICE_MAPPING:
            raise ValueError(f"Unsupported language code: {language}")
            
        voice_map = self.VOICE_MAPPING[language]
        if gender not in voice_map:
            raise ValueError(f"Unsupported gender '{gender}' for language '{language}'")
            
        voice = voice_map[gender]
        
        output_path = Path(output_dir)
        if not output_path.exists():
            raise ValueError(f"Output directory does not exist: {output_dir}")
        
        if not output_path.is_dir():
            raise ValueError(f"Output path is not a directory: {output_dir}")
        
        audio_filename = f"{chunk.chunk_number}.mp3"
        audio_file_path = output_path / audio_filename
        
        async def _generate() -> None:
            try:
                communicate = edge_tts.Communicate(chunk.text_content, voice)
                await communicate.save(str(audio_file_path))
            except Exception as async_err:
                 # Re-raise to be caught by main try block
                 raise async_err
            
        try:
            # Reuse thread-local event loop
            loop = self._get_or_create_loop()
            loop.run_until_complete(_generate())
            
        except Exception as e:
            logging.error(f"Failed to generate audio for chunk {chunk.chunk_number}: {e}")
            raise Exception(f"Failed to generate audio for chunk {chunk.chunk_number}: {str(e)}") from e
        
        return str(audio_file_path.absolute())
