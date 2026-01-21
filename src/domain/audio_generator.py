"""
Audio generation service using Edge TTS.

This module handles conversion of text chunks to audio files.
"""

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, List
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
        self._edge_tts = None
        
        # Initialize single background loop
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._start_background_loop,
            args=(self._loop,),
            daemon=True
        )
        self._loop_thread.start()

    def _start_background_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Runs the asyncio loop in a separate thread."""
        asyncio.set_event_loop(loop)
        loop.run_forever()
    
    @property
    def edge_tts(self) -> Any:
        """Lazy load edge_tts module."""
        if self._edge_tts is None:
            import edge_tts
            self._edge_tts = edge_tts
        return self._edge_tts
    
    # Removed _get_or_create_loop as we use a single loop now

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
        # Batch size 1 implementation using the new batch logic
        results = self.generate_audio_batch(
            [chunk],
            language,
            gender,
            output_dir
        )
        return results[0]

    def generate_audio_batch(
            self,
            chunks: List[AudioChunk],
            language: str,
            gender: str,
            output_dir: str
    ) -> List[str]:
        """
        Generates multiple audio files from text chunks concurrently.
        
        Args:
            chunks: List of AudioChunk objects
            language: Language code
            gender: Voice gender
            output_dir: Output directory
            
        Returns:
            List of absolute paths to generated audio files
            
        Raises:
            ValueError: If parameters are invalid
            Exception: If generation fails for any chunk
        """
        if not chunks:
            return []
            
        if language not in self.VOICE_MAPPING:
            raise ValueError(f"Unsupported language code: {language}")
            
        voice_map = self.VOICE_MAPPING[language]
        if gender not in voice_map:
            raise ValueError(f"Unsupported gender '{gender}' for language '{language}'")
            
        voice = voice_map[gender]
        
        output_path = Path(output_dir)
        if not output_path.exists() or not output_path.is_dir():
            raise ValueError(f"Invalid output directory: {output_dir}")

        async def _generate_one(c: AudioChunk) -> str:
            audio_path = output_path / f"{c.chunk_number}.mp3"
            communicate = self.edge_tts.Communicate(c.text_content, voice)
            await communicate.save(str(audio_path))
            return str(audio_path.absolute())

        async def _generate_all() -> List[str]:
            return await asyncio.gather(
                *[_generate_one(c) for c in chunks]
            )

        try:
            future = asyncio.run_coroutine_threadsafe(
                _generate_all(), 
                self._loop
            )
            return future.result()
        except Exception as e:
            logging.error(f"Batch generation failed: {e}")
            raise Exception(f"Batch generation failed: {str(e)}") from e
