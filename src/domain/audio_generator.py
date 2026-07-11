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
        },
        'fr': {
            'male': 'fr-FR-HenriNeural',
            'female': 'fr-FR-DeniseNeural'
        },
        'es': {
            'male': 'es-ES-AlvaroNeural',
            'female': 'es-ES-ElviraNeural'
        },
        'it': {
            'male': 'it-IT-ValerioNeural',
            'female': 'it-IT-ElsaNeural'
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
    ) -> tuple[str, float]:
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

    async def _generate_one_with_retry(
        self,
        chunk: AudioChunk,
        voice: str,
        output_path: Path,
        max_retries: int = 5,
        backoff: float = 2.0
    ) -> tuple[str, float]:
        """Generates audio for a single chunk with exponential backoff retry logic."""
        last_exception = None
        for attempt in range(max_retries):
            try:
                audio_path = output_path / f"{chunk.chunk_number}.mp3"
                communicate = self.edge_tts.Communicate(chunk.text_content, voice)
                
                async with self._semaphore:
                    try:
                        await asyncio.wait_for(communicate.save(str(audio_path)), timeout=60)
                    except asyncio.TimeoutError as e:
                        logging.error(f"Timeout while generating audio for chunk {chunk.chunk_number}")
                        raise Exception(f"Timeout while generating audio for chunk {chunk.chunk_number}") from e
                    
                    duration = self._get_file_duration_fast(str(audio_path))
                
                return str(audio_path.absolute()), duration
            except Exception as e:
                last_exception = e
                logging.warning(
                    f"Attempt {attempt + 1}/{max_retries} failed for chunk {chunk.chunk_number}: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff * (2 ** attempt))
        
        raise last_exception

    def generate_audio_batch(
            self,
            chunks: List[AudioChunk],
            language: str,
            gender: str,
            output_dir: str,
            chunk_callback: Optional[Any] = None,
            max_workers: int = 5,
            max_retries: int = 5,
            backoff: float = 2.0
    ) -> List[tuple[str, float]]:
        """
        Generates multiple audio files from text chunks concurrently with rate limiting and retry.
        Uses asyncio.Queue and an internal worker pool to avoid thundering herd.
        """
        if not chunks:
            return []
            
        if language not in self.VOICE_MAPPING:
            raise ValueError(f"Unsupported language code: {language}")
            
        voice_map = self.VOICE_MAPPING[language]
        voice = voice_map[gender]
        
        output_path = Path(output_dir)

        async def _generate_all_with_queue() -> List[tuple[str, float]]:
            queue = asyncio.Queue()
            for idx, c in enumerate(chunks):
                await queue.put((idx, c))
                
            results = [None] * len(chunks)
            num_workers = min(max_workers, len(chunks))
            
            async def worker():
                while not queue.empty():
                    try:
                        idx, chunk = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                        
                    try:
                        res = await self._generate_one_with_retry(
                            chunk, voice, output_path,
                            max_retries=max_retries,
                            backoff=backoff
                        )
                        results[idx] = res
                        if chunk_callback:
                            try:
                                chunk_callback(chunk.chunk_number, res[0], res[1])
                            except Exception as cb_err:
                                logging.error(f"Error in chunk callback: {cb_err}")
                    except Exception as e:
                        results[idx] = e
                    finally:
                        queue.task_done()
                        
            workers = [asyncio.create_task(worker()) for _ in range(num_workers)]
            await asyncio.gather(*workers)
            
            for res in results:
                if isinstance(res, Exception):
                    raise res
            return results

        try:
            future = asyncio.run_coroutine_threadsafe(
                _generate_all_with_queue(), 
                self._loop
            )
            return future.result()
        except Exception as e:
            logging.error(f"Batch generation failed: {e}")
            raise Exception(f"Batch generation failed: {str(e)}") from e

    def _get_file_duration_fast(self, file_path: str) -> float:
        """Fast duration extraction using mutagen."""
        try:
            from mutagen.mp3 import MP3
            audio = MP3(file_path)
            return float(audio.info.length)
        except Exception:
            return 0.0

    def close(self) -> None:
        """Stops and closes the background asyncio event loop."""
        if hasattr(self, '_loop') and self._loop:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if hasattr(self, '_loop_thread') and self._loop_thread.is_alive():
                self._loop_thread.join(timeout=2.0)
            try:
                self._loop.close()
            except Exception as e:
                logging.warning(f"Error closing AudioGenerator event loop: {e}")

