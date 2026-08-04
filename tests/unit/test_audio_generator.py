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
        supported_langs = ['en', 'uk', 'de', 'ru', 'fr', 'es', 'it']
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
        call_args = self.mock_communicate.call_args
        self.assertEqual(call_args.args[0], "Test text")
        self.assertEqual(call_args.args[1], expected_voice)
        
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
            self.generator.generate_audio_batch([chunk], "ja", "male", "/tmp")

    def test_generate_audio_batch_failure_handling(self):
        """
        Verifies per-chunk failure isolation (BUG-2 fix).

        With the updated AudioGenerator, individual chunk failures no longer
        abort the entire batch.  A permanently failing chunk produces a ``None``
        entry in the results list.  The batch call itself succeeds (no exception
        is raised at the batch level).

        A generic Exception with no transient keywords (ISSUE-11 fix) is treated
        as fatal and aborts retrying after the first attempt.
        """
        self.mock_instance.save.side_effect = Exception("API Error")
        chunk = AudioChunk(chunk_number=1, text_content="text")

        # Should NOT raise — failed chunk returns None in results
        results = self.generator.generate_audio_batch(
            [chunk], "en", "male", "/tmp",
            max_retries=2, backoff=0.01
        )

        # The result list has one entry and it is None (chunk failed)
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0])

        # ISSUE-11: generic 'API Error' is treated as FATAL — no retries.
        # So TTS is called exactly once (no retry loop).
        self.assertEqual(self.mock_instance.save.call_count, 1)

    def test_generate_audio_batch_transient_error_retries(self):
        """
        Verifies that transient errors (matching known patterns) are retried
        up to max_retries times (ISSUE-10 / ISSUE-11 fix).
        """
        # 'timeout' keyword triggers the transient classification
        self.mock_instance.save.side_effect = Exception("connection timeout")
        chunk = AudioChunk(chunk_number=1, text_content="text")

        results = self.generator.generate_audio_batch(
            [chunk], "en", "male", "/tmp",
            max_retries=2, backoff=0.01
        )

        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0])

        # Transient error — should have retried max_retries (2) times
        self.assertEqual(self.mock_instance.save.call_count, 2)

    def test_max_concurrency_configuration(self):
        """test_max_concurrency_configuration: Verifies that max_concurrency is set correctly and defaults to 3."""
        self.assertEqual(self.generator._max_concurrency, 3)
        
        custom_generator = AudioGenerator(max_concurrency=5)
        try:
            self.assertEqual(custom_generator._max_concurrency, 5)
        finally:
            custom_generator.close()

    def test_global_rate_limit_recheck_inside_semaphore(self):
        """
        Verifies that if a global rate limit is set while a worker is waiting for the semaphore,
        it waits out the rate-limit delay inside the semaphore and completes synthesis.
        """
        # Set max_concurrency to 1 so chunk 2 is forced to wait on the semaphore while chunk 1 is running
        generator = AudioGenerator(max_concurrency=1)
        try:
            chunk1 = AudioChunk(chunk_number=1, text_content="first")
            chunk2 = AudioChunk(chunk_number=2, text_content="second")

            calls = []
            async def mock_save(path):
                # When chunk 1 runs, set global rate limit reset time slightly in the future
                if "1.mp3" in path:
                    generator._rate_limit_reset_time = asyncio.get_event_loop().time() + 0.1
                    calls.append("chunk1")
                else:
                    calls.append("chunk2")

            self.mock_instance.save.side_effect = mock_save

            results = generator.generate_audio_batch(
                [chunk1, chunk2], "en", "male", "/tmp",
                max_workers=2, max_retries=1, backoff=0.01
            )
            
            self.assertEqual(len(results), 2)
            self.assertIsNotNone(results[0])
            self.assertIsNotNone(results[1])
            self.assertIn("chunk1", calls)
            self.assertIn("chunk2", calls)
        finally:
            generator.close()


if __name__ == '__main__':
    unittest.main()
