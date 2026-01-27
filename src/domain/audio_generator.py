"""
Audio generation service using Edge TTS.

This module handles conversion of text chunks to audio files.
"""

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, List, Optional

from src.domain.models import AudioChunk


class AudioGenerator:
    """
    Service responsible for generating audio files from text using Edge TTS.
    
    This service integrates with Microsoft Edge's Text-to-Speech API.
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
        
        # Rate limiting semaphore (shared across all threads using this instance)
        # We initialize it inside the loop to ensure it's bound to the correct loop
        self._semaphore: Optional[asyncio.Semaphore] = None
        
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
        # Initialize semaphore in the loop context
        self._semaphore = asyncio.Semaphore(5) 
        loop.run_forever()
    
    @property
    def edge_tts(self) -> Any:
        """Lazy load edge_tts module."""
        if self._edge_tts is None:
            import edge_tts
            self._edge_tts = edge_tts
        return self._edge_tts

    def generate_audio(
            self,
            chunk: AudioChunk,
            language: str,
            gender: str,
            output_dir: str
    ) -> str:
        """
        Generates an audio file from a text chunk.
        """
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
        Generates multiple audio files from text chunks concurrently with rate limiting.
        """
        if not chunks:
            return []
            
        if language not in self.VOICE_MAPPING:
            raise ValueError(f"Unsupported language code: {language}")
            
        voice_map = self.VOICE_MAPPING[language]
        voice = voice_map[gender]
        
        output_path = Path(output_dir)

        async def _generate_one(c: AudioChunk) -> str:
            audio_path = output_path / f"{c.chunk_number}.mp3"
            communicate = self.edge_tts.Communicate(c.text_content, voice)
            
            # Use the internal semaphore to limit concurrent API requests
            async with self._semaphore:
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

