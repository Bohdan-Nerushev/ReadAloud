"""
Real End-to-End integration tests for Piper TTS mode.

These tests execute the full ReadAloud application stack with real Docker container
and real Piper local synthesis when PIPER_INTEGRATION_TESTS=1 is set.

Run with: PIPER_INTEGRATION_TESTS=1 pytest tests/integration/test_piper_e2e_real.py -v
"""

import os
import time
from pathlib import Path
from typing import Generator

import pytest

from src.domain.models import ProjectConfig, TtsBackend, TaskStatus
from src.infrastructure.docker_manager import DockerManager, PIPER_DEFAULT_PORT
from src.infrastructure.piper_model_manager import PiperModelManager, DEFAULT_MODELS_DIR
from src.infrastructure.ioc import Container

PIPER_TESTS_ENABLED = os.environ.get("PIPER_INTEGRATION_TESTS", "0") == "1"

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not PIPER_TESTS_ENABLED, reason="Set PIPER_INTEGRATION_TESTS=1 to enable")
class TestPiperRealE2E:
    """Real End-to-End integration test with Docker and Wyoming Piper."""

    def test_full_application_piper_task_synthesis(self, tmp_path: Path):
        """
        Full E2E test running ReadAloudApplication with Piper backend:
        1. Reads input text file.
        2. Configures ProjectConfig with TtsBackend.PIPER.
        3. Spawns tasks via ApplicationController.
        4. Verifies Docker container lifecycle and audio output.
        """
        docker_manager = DockerManager()
        if not docker_manager.is_docker_available():
            pytest.skip("Docker is not available.")
        if not docker_manager.is_piper_image_available():
            pytest.skip("rhasspy/wyoming-piper image not available locally.")

        model_manager = PiperModelManager()
        if not model_manager.is_model_present("ru_RU-dmitri-medium"):
            pytest.skip("Voice model 'ru_RU-dmitri-medium' not present.")

        # Create input text file
        input_file = tmp_path / "e2e_input.txt"
        input_file.write_text("Привет! Это реальный end-to-end тест нейромережевого озвучування.", encoding="utf-8")

        output_dir = tmp_path / "output"
        output_dir.mkdir(exist_ok=True)

        config = ProjectConfig(
            project_name="Piper_Real_E2E",
            input_file_path=str(input_file),
            language="ru",
            gender="male",
            speed=1.0,
            thread_count=1,
            output_dir_path=str(output_dir),
            tts_backend=TtsBackend.PIPER,
        )

        container = Container(state_file_path=str(tmp_path / "state.json"))
        controller = container.app_controller

        try:
            # Submit task
            controller.add_task(config)

            # Wait for preparation worker
            if controller._prep_worker is not None:
                controller._prep_worker.wait(timeout=10000)

            # Wait for generation and assembly to finish (up to 45 seconds)
            deadline = time.time() + 45
            completed = False
            while time.time() < deadline:
                current = controller.get_current_task()
                all_tasks = container.queue_service.get_all_tasks()
                if not current and not any(t.status in (TaskStatus.PROCESSING, TaskStatus.PENDING) for t in all_tasks):
                    completed = True
                    break
                time.sleep(1)

            assert completed, "Task did not complete within 45 seconds."

            # Verify output MP3 file exists in output_dir
            mp3_files = list(output_dir.glob("*.mp3"))
            assert len(mp3_files) >= 1
            output_mp3 = mp3_files[0]
            assert output_mp3.stat().st_size > 0

        finally:
            controller.shutdown()
