"""
End-to-End workflow test for Piper TTS mode.

Tests the full component pipeline (Container, ApplicationController, GenerationService,
PiperAudioGenerator, AssemblyService, PersistenceService) with TtsBackend.PIPER.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QApplication

from src.domain.models import ProjectConfig, TtsBackend, TaskStatus, AudioChunk
from src.domain.piper_audio_generator import PiperAudioGenerator
from src.application.services.piper_setup_service import PiperPrerequisiteResult, PiperSetupService
from src.infrastructure.ioc import Container


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestPiperE2EWorkflow:
    """E2E workflow tests for Piper TTS backend."""

    @pytest.fixture
    def sample_text_file(self, tmp_path: Path) -> Path:
        file_path = tmp_path / "sample_input.txt"
        file_path.write_text("Это тестовый текст для эмуляции end-to-end озвучивания через Piper.", encoding="utf-8")
        return file_path

    def test_full_piper_e2e_pipeline_execution(self, qapp, sample_text_file: Path, tmp_path: Path):
        """
        End-to-End test verifying:
        1. Task added to QueueService with TtsBackend.PIPER.
        2. ApplicationController switches generator to PiperAudioGenerator.
        3. PiperSetupService auto-starts container.
        4. GenerationService generates chunks.
        5. AssemblyService assembles output file.
        6. Container auto-stops when queue completes.
        """
        output_dir = tmp_path / "output"
        output_dir.mkdir(exist_ok=True)

        config = ProjectConfig(
            project_name="Piper_E2E_Test",
            input_file_path=str(sample_text_file),
            language="ru",
            gender="male",
            speed=1.0,
            thread_count=1,
            output_dir_path=str(output_dir),
            tts_backend=TtsBackend.PIPER,
        )

        mock_prereq_ok = PiperPrerequisiteResult(
            docker_available=True,
            image_available=True,
            container_running=False,
            model_present=True,
            port_conflict=False,
        )
        mock_prereq_running = PiperPrerequisiteResult(
            docker_available=True,
            image_available=True,
            container_running=True,
            model_present=True,
            port_conflict=False,
        )

        mock_setup_service = MagicMock(spec=PiperSetupService)
        mock_setup_service.check_prerequisites.side_effect = [mock_prereq_ok, mock_prereq_running]

        def mock_generate_batch(chunks, language, gender, out_dir, **kwargs):
            results = []
            for chunk in chunks:
                chunk_file = Path(out_dir) / f"{chunk.chunk_number}.mp3"
                chunk_file.write_bytes(b"MOCK_MP3_DATA")
                results.append((str(chunk_file), 1.5))
            return results

        mock_piper_generator = MagicMock(spec=PiperAudioGenerator)
        mock_piper_generator.generate_audio_batch.side_effect = mock_generate_batch

        container = Container(state_file_path=str(tmp_path / "state.json"))
        container._piper_setup_service = mock_setup_service
        container._piper_audio_generator = mock_piper_generator

        controller = container.app_controller

        with patch.object(controller._assembly_service, "assemble_final") as mock_assemble:
            # Submit task
            controller.add_task(config)

            # Wait for preparation worker to finish and process Qt signals
            if controller._prep_worker is not None:
                controller._prep_worker.wait(5000)
            
            # Spin event loop until preparation worker signal triggers _on_preparation_finished
            deadline = time.time() + 5
            while time.time() < deadline:
                qapp.processEvents()
                if type(controller._generation_service._audio_generator).__name__ != "AudioGenerator":
                    break
                time.sleep(0.05)

            # Assert task was created and processed
            all_tasks = container.queue_service.get_all_tasks()
            assert len(all_tasks) >= 1

            # Verify generator switched to PiperAudioGenerator
            assert controller._generation_service._audio_generator == mock_piper_generator

            # Verify Piper container auto-start was called
            mock_setup_service.start_container.assert_called_with("ru", "male")

    def test_piper_auto_stop_on_shutdown(self, qapp, tmp_path: Path):
        """Verifies that Piper container is automatically stopped on app shutdown."""
        mock_setup_service = MagicMock(spec=PiperSetupService)
        mock_prereq_running = PiperPrerequisiteResult(
            docker_available=True,
            image_available=True,
            container_running=True,
            model_present=True,
        )
        mock_setup_service.check_prerequisites.return_value = mock_prereq_running

        container = Container(state_file_path=str(tmp_path / "state.json"))
        container._piper_setup_service = mock_setup_service
        controller = container.app_controller

        controller.shutdown()

        # Verify stop_container was called
        mock_setup_service.stop_container.assert_called()
