import unittest
import sys
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# Mock edge_tts dependency before any imports that might use it
mock_edge_tts = MagicMock()
sys.modules['edge_tts'] = mock_edge_tts

from src.domain.audio_generator import AudioGenerator
from src.domain.models import AudioChunk

class TestAudioGenerator(unittest.TestCase):
    def setUp(self):
        # We need to mock edge_tts before initializing AudioGenerator 
        # because AudioGenerator might import it or start a loop that uses it.
        self.patcher = patch('edge_tts.Communicate')
        self.mock_communicate = self.patcher.start()
        
        # Mock communicate.save and make it an AsyncMock
        self.mock_instance = MagicMock()
        self.mock_instance.save = AsyncMock()
        self.mock_communicate.return_value = self.mock_instance
        
        self.generator = AudioGenerator()

    def tearDown(self):
        self.patcher.stop()
        self.generator.close()

    def test_voice_mapping_coverage(self):
        """test_voice_mapping_coverage: Verifies mapping existence for all supported languages (en, uk, de, ru)."""
        supported_langs = ['en', 'uk', 'de', 'ru']
        for lang in supported_langs:
            self.assertIn(lang, self.generator.VOICE_MAPPING)
            self.assertIn('male', self.generator.VOICE_MAPPING[lang])
            self.assertIn('female', self.generator.VOICE_MAPPING[lang])

    def test_generate_audio_batch_calls_tts(self):
        """test_generate_audio_batch_calls_tts: Verifies that the method calls edge_tts.Communicate."""
        chunk = AudioChunk(chunk_number=1, text_content="Test text")
        self.generator.generate_audio_batch([chunk], "en", "male", "/tmp")
        
        # Verify Communicate was called with correct text and voice
        expected_voice = self.generator.VOICE_MAPPING['en']['male']
        self.mock_communicate.assert_called_with("Test text", expected_voice)
        
        # Verify save was called
        # The save call is inside the async loop, but since we used future.result() it should have finished
        self.mock_instance.save.assert_called()

    def test_generate_audio_batch_returns_paths(self):
        """test_generate_audio_batch_returns_paths: Verifies that a list of absolute output file paths is returned."""
        chunk1 = AudioChunk(chunk_number=1, text_content="Text 1")
        chunk2 = AudioChunk(chunk_number=2, text_content="Text 2")
        
        results = self.generator.generate_audio_batch([chunk1, chunk2], "uk", "female", "/tmp")
        
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0][0].endswith("1.mp3"))
        self.assertTrue(results[1][0].endswith("2.mp3"))
        self.assertTrue(Path(results[0][0]).is_absolute())

    def test_generate_audio_batch_unsupported_lang(self):
        """test_generate_audio_batch_unsupported_lang: Verifies ValueError is raised for an unsupported language."""
        chunk = AudioChunk(chunk_number=1, text_content="text")
        with self.assertRaises(ValueError):
            self.generator.generate_audio_batch([chunk], "fr", "male", "/tmp")

    def test_generate_audio_batch_failure_handling(self):
        """test_generate_audio_batch_failure_handling: Verifies correct retry and error handling on API failure."""
        self.mock_instance.save.side_effect = Exception("API Error")
        chunk = AudioChunk(chunk_number=1, text_content="text")
        with self.assertRaises(Exception) as cm:
            self.generator.generate_audio_batch(
                [chunk], "en", "male", "/tmp",
                max_retries=2, backoff=0.01
            )
        self.assertIn("Batch generation failed", str(cm.exception))

if __name__ == '__main__':
    unittest.main()
