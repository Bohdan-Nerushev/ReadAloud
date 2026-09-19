"""
Unit tests for Piper container lifecycle management.

Verifies:
  1. Docker container is started only when the user selects Piper mode and starts task generation.
  2. Docker container is automatically stopped when queue processing completes (queue becomes idle).
  3. Docker container is automatically stopped on application exit / shutdown.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.application.app_controller import ApplicationController
from src.domain.models import ProjectConfig, TtsBackend, TaskStatus, GenerationTask


class TestPiperLifecycle:
    @pytest.fixture
    def mock_services(self):
        queue_service = MagicMock()
        text_processor = MagicMock()
        text_chunker = MagicMock()
        file_manager = MagicMock()
        generation_service = MagicMock()
        assembly_service = MagicMock()
        persistence_service = MagicMock()
        piper_setup_service = MagicMock()

        # Mock prereq check return value
        prereq = MagicMock()
        prereq.container_running = False
        prereq.can_start_container = True
        prereq.docker_available = True
        prereq.image_available = True
        prereq.model_present = True
        prereq.port_conflict = False
        piper_setup_service.check_prerequisites.return_value = prereq

        return {
            "queue_service": queue_service,
            "text_processor": text_processor,
            "text_chunker": text_chunker,
            "file_manager": file_manager,
            "generation_service": generation_service,
            "assembly_service": assembly_service,
            "persistence_service": persistence_service,
            "piper_setup_service": piper_setup_service,
        }

    def test_start_container_called_when_task_generation_starts_in_piper_mode(self, mock_services, tmp_path):
        """
        Piper container must be started only when task generation starts for a Piper task.
        """
        controller = ApplicationController(
            queue_service=mock_services["queue_service"],
            text_processor=mock_services["text_processor"],
            text_chunker=mock_services["text_chunker"],
            file_manager=mock_services["file_manager"],
            generation_service=mock_services["generation_service"],
            assembly_service=mock_services["assembly_service"],
            persistence_service=mock_services["persistence_service"],
            piper_setup_service=mock_services["piper_setup_service"],
        )

        input_file = tmp_path / "test.txt"
        input_file.write_text("sample text")

        config = ProjectConfig(
            project_name="test_proj",
            input_file_path=str(input_file),
            language="ru",
            gender="male",
            thread_count=1,
            output_dir_path=str(tmp_path),
            tts_backend=TtsBackend.PIPER,
        )
        task = GenerationTask(config=config)
        mock_services["queue_service"].get_current_task.return_value = task

        # Call _start_generation_process directly
        controller._start_generation_process()

        # Check that start_container was called with task language and gender
        mock_services["piper_setup_service"].start_container.assert_called_once_with("ru", "male")

    def test_auto_stop_piper_when_queue_is_idle(self, mock_services):
        """
        When all tasks in queue finish processing and no pending/processing tasks remain,
        _auto_stop_piper_if_idle must call stop_container.
        """
        controller = ApplicationController(
            queue_service=mock_services["queue_service"],
            text_processor=mock_services["text_processor"],
            text_chunker=mock_services["text_chunker"],
            file_manager=mock_services["file_manager"],
            generation_service=mock_services["generation_service"],
            assembly_service=mock_services["assembly_service"],
            persistence_service=mock_services["persistence_service"],
            piper_setup_service=mock_services["piper_setup_service"],
        )

        # Queue returns no active or pending tasks
        mock_services["queue_service"].get_all_tasks.return_value = []

        controller._auto_stop_piper_if_idle()

        mock_services["piper_setup_service"].stop_container.assert_called_once()

    def test_stop_piper_on_app_shutdown(self, mock_services):
        """
        When the application shuts down, shutdown() must call stop_container unconditionally.
        """
        controller = ApplicationController(
            queue_service=mock_services["queue_service"],
            text_processor=mock_services["text_processor"],
            text_chunker=mock_services["text_chunker"],
            file_manager=mock_services["file_manager"],
            generation_service=mock_services["generation_service"],
            assembly_service=mock_services["assembly_service"],
            persistence_service=mock_services["persistence_service"],
            piper_setup_service=mock_services["piper_setup_service"],
        )

        controller.shutdown()

        mock_services["piper_setup_service"].stop_container.assert_called_once()
