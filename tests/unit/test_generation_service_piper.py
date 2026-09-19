"""
Unit tests for GenerationService with Piper backend.

Verifies that GenerationService works identically with PiperAudioGenerator
(via TtsGeneratorProtocol) as it does with AudioGenerator.
"""

from typing import List, Optional, Tuple, Callable
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from src.application.services.generation_service import GenerationService
from src.domain.models import AudioChunk, GenerationTask, ProjectConfig, TtsBackend
from src.domain.tts_generator_protocol import TtsGeneratorProtocol


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def make_config(backend: TtsBackend = TtsBackend.PIPER) -> ProjectConfig:
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a dummy input file
        input_file = os.path.join(tmpdir, "input.txt")
        with open(input_file, "w") as f:
            f.write("Test text")
        return ProjectConfig(
            project_name="test_project",
            input_file_path=input_file,
            language="ru",
            gender="male",
            thread_count=1,
            output_dir_path=tmpdir,
            tts_backend=backend,
        )


class FakePiperGenerator:
    """
    A minimal TtsGeneratorProtocol-conforming fake for testing.
    Satisfies the Protocol without inheriting from any class.
    """

    def __init__(self, results: List[Optional[Tuple[str, float]]]) -> None:
        self.results = results
        self.call_count = 0

    def generate_audio(
            self, chunk: AudioChunk, language: str, gender: str, output_dir: str
    ) -> Tuple[str, float]:
        return self.generate_audio_batch([chunk], language, gender, output_dir)[0]

    def generate_audio_batch(
            self,
            chunks: List[AudioChunk],
            language: str,
            gender: str,
            output_dir: str,
            chunk_callback: Optional[Callable[[int, str, float], None]] = None,
            max_workers: int = 10,
            max_retries: int = 10,
            backoff: float = 2.0,
    ) -> List[Optional[Tuple[str, float]]]:
        self.call_count += 1
        batch_results = self.results[:len(chunks)]
        if chunk_callback:
            for chunk, res in zip(chunks, batch_results):
                if res is not None:
                    chunk_callback(chunk.chunk_number, res[0], res[1])
        return batch_results

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    def test_piper_generator_satisfies_protocol(self):
        """FakePiperGenerator (and PiperAudioGenerator) must satisfy TtsGeneratorProtocol."""
        fake = FakePiperGenerator(results=[("/tmp/1.mp3", 1.5)])
        assert isinstance(fake, TtsGeneratorProtocol)

    def test_generation_service_accepts_fake_piper_generator(self):
        """GenerationService constructor accepts any TtsGeneratorProtocol implementation."""
        fake = FakePiperGenerator(results=[])
        service = GenerationService(audio_generator=fake)
        assert service._audio_generator is fake


# ---------------------------------------------------------------------------
# GenerationService with Piper
# ---------------------------------------------------------------------------

class TestGenerationServiceWithPiper:
    def test_chunk_generated_signal_emitted(self):
        """chunkGenerated signal fires when PiperAudioGenerator returns a result."""
        fake = FakePiperGenerator(results=[("/tmp/1.mp3", 2.0), ("/tmp/2.mp3", 1.5)])
        service = GenerationService(audio_generator=fake)

        emitted = []
        service.chunkGenerated.connect(lambda n, p, d: emitted.append((n, p, d)))

        chunks = [
            AudioChunk(chunk_number=1, text_content="Chunk one"),
            AudioChunk(chunk_number=2, text_content="Chunk two"),
        ]

        import tempfile
        with tempfile.TemporaryDirectory() as output_dir:
            service._output_dir = output_dir
            service._is_stopped = False
            service._generate_batch_safe(
                batch=chunks,
                language="ru",
                gender="male",
                correlation_id="test-id",
                max_workers=1,
            )

        assert len(emitted) == 2

    def test_chunk_failed_signal_emitted_for_none_result(self):
        """chunkFailed signal fires for each chunk that returned None."""
        fake = FakePiperGenerator(results=[None, ("/tmp/2.mp3", 1.0)])
        service = GenerationService(audio_generator=fake)

        failed = []
        service.chunkFailed.connect(lambda n, msg: failed.append(n))

        chunks = [
            AudioChunk(chunk_number=1, text_content="Fail"),
            AudioChunk(chunk_number=2, text_content="Success"),
        ]

        import tempfile
        with tempfile.TemporaryDirectory() as output_dir:
            service._output_dir = output_dir
            service._is_stopped = False
            service._generate_batch_safe(
                batch=chunks,
                language="ru",
                gender="male",
                correlation_id="test-id",
                max_workers=1,
            )

        assert 1 in failed
        assert 2 not in failed

    def test_batch_generated_signal_emitted(self):
        """batchGenerated signal carries successful (chunk_number, path, duration) tuples."""
        fake = FakePiperGenerator(results=[("/tmp/1.mp3", 3.0)])
        service = GenerationService(audio_generator=fake)

        batch_results = []
        service.batchGenerated.connect(lambda r: batch_results.extend(r))

        chunks = [AudioChunk(chunk_number=1, text_content="Test")]

        import tempfile
        with tempfile.TemporaryDirectory() as output_dir:
            service._output_dir = output_dir
            service._is_stopped = False
            service._generate_batch_safe(
                batch=chunks,
                language="ru",
                gender="male",
                correlation_id="test-id",
                max_workers=1,
            )

        assert len(batch_results) == 1
        chunk_num, path, duration = batch_results[0]
        assert chunk_num == 1
        assert path == "/tmp/1.mp3"
        assert duration == 3.0

    def test_stopped_flag_prevents_execution(self):
        """If _is_stopped is True, _generate_batch_safe returns without calling the generator."""
        fake = FakePiperGenerator(results=[("/tmp/1.mp3", 1.0)])
        service = GenerationService(audio_generator=fake)
        service._is_stopped = True

        import tempfile
        with tempfile.TemporaryDirectory() as output_dir:
            service._output_dir = output_dir
            service._generate_batch_safe(
                batch=[AudioChunk(chunk_number=1, text_content="x")],
                language="ru",
                gender="male",
                correlation_id="id",
                max_workers=1,
            )

        assert fake.call_count == 0
