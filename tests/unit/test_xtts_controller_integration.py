"""
Unit tests for ApplicationController integration with XTTS-v2 setup and lifecycle.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import tempfile

from src.application.app_controller import ApplicationController
from src.application.services.xtts_setup_service import XttsSetupService
from src.domain.models import ProjectConfig, GenerationTask, TaskStatus, TtsBackend
from src.infrastructure.xtts_model_manager import XttsDiagnosticsResult


class TestAppControllerXttsIntegration(unittest.TestCase):
    """Tests XTTS setup service integration inside ApplicationController."""

    def setUp(self):
        self.queue_service = MagicMock()
        self.text_processor = MagicMock()
        self.text_chunker = MagicMock()
        self.file_manager = MagicMock()
        self.generation_service = MagicMock()
        self.assembly_service = MagicMock()
        self.persistence_service = MagicMock()
        self.piper_setup_service = MagicMock()
        self.xtts_setup_service = MagicMock(spec=XttsSetupService)

        self.audio_generator_resolver = MagicMock()

        self.controller = ApplicationController(
            queue_service=self.queue_service,
            text_processor=self.text_processor,
            text_chunker=self.text_chunker,
            file_manager=self.file_manager,
            generation_service=self.generation_service,
            assembly_service=self.assembly_service,
            persistence_service=self.persistence_service,
            audio_generator_resolver=self.audio_generator_resolver,
            piper_setup_service=self.piper_setup_service,
            xtts_setup_service=self.xtts_setup_service,
        )

    def test_start_generation_process_checks_xtts_prerequisites(self):
        """When starting task with XTTS backend, prerequisites must be checked and logged."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp_file:
            tmp_file.write("Test content")
            tmp_file_path = tmp_file.name

        try:
            config = ProjectConfig(
                project_name="xtts_test",
                input_file_path=tmp_file_path,
                language="uk",
                gender="female",
                thread_count=1,
                output_dir_path="/tmp",
                tts_backend=TtsBackend.XTTS,
            )
            task = GenerationTask(config=config)
            self.queue_service.get_current_task.return_value = task

            diag_result = XttsDiagnosticsResult(
                tts_package_installed=True,
                cuda_available=True,
                vram_free_gb=8.0,
                vram_total_gb=12.0,
                vram_sufficient=True,
                model_cached=True,
            )
            self.xtts_setup_service.check_prerequisites.return_value = diag_result

            mock_generator = MagicMock()
            self.audio_generator_resolver.return_value = mock_generator

            self.controller._start_generation_process()

            self.audio_generator_resolver.assert_called_once_with(TtsBackend.XTTS)
            self.generation_service.switch_generator.assert_called_once_with(mock_generator)
            self.xtts_setup_service.check_prerequisites.assert_called_once()
            self.generation_service.start_generation.assert_called_once()
        finally:
            Path(tmp_file_path).unlink(missing_ok=True)

    def test_shutdown_unloads_xtts_model(self):
        """Application shutdown must trigger XTTS model unload to free GPU VRAM."""
        self.controller.shutdown()

        self.xtts_setup_service.unload_model.assert_called_once()
        self.piper_setup_service.stop_container.assert_called_once()

    def test_shutdown_handles_xtts_unload_exception_gracefully(self):
        """If unloading XTTS model fails during shutdown, error is caught and logged."""
        self.xtts_setup_service.unload_model.side_effect = RuntimeError("VRAM flush error")

        # Should not raise exception
        self.controller.shutdown()

        self.xtts_setup_service.unload_model.assert_called_once()


if __name__ == "__main__":
    unittest.main()
