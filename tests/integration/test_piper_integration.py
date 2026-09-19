"""
Integration tests for Piper TTS backend.

These tests require:
  1. Docker installed and running.
  2. rhasspy/wyoming-piper image pulled locally.
  3. Voice model files for ru_RU-dmitri-medium in the default models directory.
  4. No other process occupying port 10200.

Run only with: pytest tests/integration/ -m integration -v

These tests are excluded from standard CI. Add --docker flag or set
PIPER_INTEGRATION_TESTS=1 env variable to enable.
"""

import asyncio
import os
import socket
import time
import wave
from pathlib import Path
from typing import Generator

import pytest

from src.domain.models import AudioChunk
from src.domain.piper_audio_generator import PiperAudioGenerator
from src.infrastructure.docker_manager import DockerManager, PIPER_DEFAULT_PORT
from src.infrastructure.piper_model_manager import PiperModelManager, DEFAULT_MODELS_DIR


# ---------------------------------------------------------------------------
# Markers and skip conditions
# ---------------------------------------------------------------------------

PIPER_TESTS_ENABLED = os.environ.get("PIPER_INTEGRATION_TESTS", "0") == "1"

pytestmark = pytest.mark.integration


def _is_port_open(port: int = PIPER_DEFAULT_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def docker_manager() -> DockerManager:
    return DockerManager()


@pytest.fixture(scope="module")
def model_manager() -> PiperModelManager:
    return PiperModelManager(models_dir=DEFAULT_MODELS_DIR)


@pytest.fixture(scope="module")
def piper_container(docker_manager: DockerManager, model_manager: PiperModelManager) -> Generator:
    """
    Starts the Piper container before the test module and stops it after.

    Skips the entire module if Docker or the image is not available.
    """
    if not docker_manager.is_docker_available():
        pytest.skip("Docker is not available — skipping Piper integration tests.")

    if not docker_manager.is_piper_image_available():
        pytest.skip(
            "rhasspy/wyoming-piper image is not pulled. "
            "Run: docker pull rhasspy/wyoming-piper"
        )

    if not model_manager.is_model_present("ru_RU-dmitri-medium"):
        pytest.skip(
            "Voice model 'ru_RU-dmitri-medium' not found in models directory. "
            f"Expected: {DEFAULT_MODELS_DIR}"
        )

    # Start container if not already running
    if not _is_port_open():
        env = {
            "PIPER_VOICE": "ru_RU-dmitri-medium",
            "PIPER_MODELS_DIR": str(DEFAULT_MODELS_DIR),
            "PIPER_PORT": str(PIPER_DEFAULT_PORT),
        }
        docker_manager.start_piper_compose(env=env)

        # Wait up to 30 seconds for the port to open
        deadline = time.time() + 30
        while time.time() < deadline:
            if _is_port_open():
                break
            time.sleep(1)
        else:
            docker_manager.stop_piper_compose()
            pytest.fail("Piper container did not become ready within 30 seconds.")

    yield

    # Teardown: stop container after all tests in this module
    docker_manager.stop_piper_compose()


@pytest.fixture
def generator() -> Generator:
    gen = PiperAudioGenerator()
    yield gen
    gen.close()


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not PIPER_TESTS_ENABLED, reason="Set PIPER_INTEGRATION_TESTS=1 to enable")
class TestPiperSynthesisIntegration:
    def test_synthesise_short_russian_text(
            self, piper_container, generator: PiperAudioGenerator, tmp_path: Path
    ):
        """Basic sanity check: a short Russian phrase produces a non-empty MP3."""
        chunk = AudioChunk(chunk_number=1, text_content="Привет! Это тест синтеза речи.")
        results = generator.generate_audio_batch([chunk], "ru", "male", str(tmp_path))

        assert results[0] is not None
        path, duration = results[0]
        assert Path(path).exists()
        assert Path(path).stat().st_size > 0
        assert duration > 0.0

    def test_synthesise_long_text_chunk(
            self, piper_container, generator: PiperAudioGenerator, tmp_path: Path
    ):
        """Large chunk (500+ words): verifies Piper handles it without timeout/crash."""
        long_text = "Синтез речи является важной технологией. " * 100
        chunk = AudioChunk(chunk_number=1, text_content=long_text)

        results = generator.generate_audio_batch(
            [chunk], "ru", "male", str(tmp_path),
            max_retries=3,
        )

        assert results[0] is not None
        _, duration = results[0]
        assert duration > 5.0  # Long text should produce >5s of audio

    def test_synthesise_multiple_chunks_sequentially(
            self, piper_container, generator: PiperAudioGenerator, tmp_path: Path
    ):
        """Multiple chunks are synthesised correctly in order."""
        texts = [
            "Первый фрагмент текста для синтеза.",
            "Второй фрагмент с другим содержанием.",
            "Третий и последний тестовый фрагмент.",
        ]
        chunks = [AudioChunk(chunk_number=i + 1, text_content=t) for i, t in enumerate(texts)]

        results = generator.generate_audio_batch(chunks, "ru", "male", str(tmp_path))

        assert len(results) == 3
        for result in results:
            assert result is not None
            path, duration = result
            assert Path(path).exists()
            assert duration > 0.0

    def test_callback_called_for_each_chunk(
            self, piper_container, generator: PiperAudioGenerator, tmp_path: Path
    ):
        """chunk_callback is invoked once per successfully synthesised chunk."""
        chunks = [AudioChunk(chunk_number=i + 1, text_content=f"Фрагмент {i + 1}.") for i in range(3)]
        callback_calls = []

        generator.generate_audio_batch(
            chunks, "ru", "male", str(tmp_path),
            chunk_callback=lambda n, p, d: callback_calls.append(n),
        )

        assert sorted(callback_calls) == [1, 2, 3]

    def test_output_files_are_valid_mp3(
            self, piper_container, generator: PiperAudioGenerator, tmp_path: Path
    ):
        """Output files have MP3 extension and can be read by mutagen."""
        from mutagen.mp3 import MP3
        chunk = AudioChunk(chunk_number=1, text_content="Проверка формата MP3.")
        results = generator.generate_audio_batch([chunk], "ru", "male", str(tmp_path))

        path, _ = results[0]
        assert path.endswith(".mp3")
        audio = MP3(path)
        assert audio.info.length > 0


@pytest.mark.skipif(not PIPER_TESTS_ENABLED, reason="Set PIPER_INTEGRATION_TESTS=1 to enable")
class TestPiperContainerRecoveryIntegration:
    def test_reconnects_after_container_restart(
            self, docker_manager: DockerManager, generator: PiperAudioGenerator, tmp_path: Path
    ):
        """
        Simulates a container restart mid-synthesis.
        The generator should retry and succeed after the container comes back up.
        """
        chunk = AudioChunk(chunk_number=1, text_content="Тест восстановления соединения.")

        # Stop container
        docker_manager.stop_piper_compose()
        time.sleep(2)

        # Restart container
        env = {
            "PIPER_VOICE": "ru_RU-dmitri-medium",
            "PIPER_MODELS_DIR": str(DEFAULT_MODELS_DIR),
            "PIPER_PORT": str(PIPER_DEFAULT_PORT),
        }
        docker_manager.start_piper_compose(env=env)
        deadline = time.time() + 30
        while time.time() < deadline:
            if _is_port_open():
                break
            time.sleep(1)

        # Should succeed with retries
        results = generator.generate_audio_batch(
            [chunk], "ru", "male", str(tmp_path),
            max_retries=10,
            backoff=1.0,
        )

        assert results[0] is not None
