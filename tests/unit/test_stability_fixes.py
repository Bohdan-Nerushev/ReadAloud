import unittest
import asyncio
import aiohttp
from pathlib import Path
from unittest.mock import MagicMock, patch
from src.domain.models import TaskStatus, AudioChunk, ProjectConfig, GenerationTask
from src.domain.audio_generator import SafeTCPConnector
from src.domain.audio_assembler import AudioAssembler
from src.infrastructure.progress_tracker import ProgressTracker
from src.application.services.generation_service import GenerationService
from src.application.app_controller import ApplicationController


class TestStabilityFixes(unittest.TestCase):

    def test_safe_tcp_connector_prevents_premature_closure(self):
        """Verifies that SafeTCPConnector remains open after a ClientSession closes."""
        async def run_test():
            connector = SafeTCPConnector()
            self.assertFalse(connector.closed)

            # Create and close a session with the connector
            async with aiohttp.ClientSession(connector=connector) as session:
                pass

            # Connector should still be open
            self.assertFalse(connector.closed)

            # Connector should only close when real_close is explicitly called
            await connector.real_close()
            self.assertTrue(connector.closed)

        asyncio.run(run_test())

    def test_controller_shutdown_and_stop_waits_for_threads(self):
        """Verifies that shutdown and stop_generation call GenerationService.stop with wait=True."""
        queue_service = MagicMock()
        text_processor = MagicMock()
        text_chunker = MagicMock()
        file_manager = MagicMock()
        generation_service = MagicMock()
        assembly_service = MagicMock()
        persistence_service = MagicMock()

        # Mock queue_service methods to return appropriate defaults
        queue_service.get_all_tasks.return_value = []
        queue_service.get_current_task.return_value = None

        controller = ApplicationController(
            queue_service=queue_service,
            text_processor=text_processor,
            text_chunker=text_chunker,
            file_manager=file_manager,
            generation_service=generation_service,
            assembly_service=assembly_service,
            persistence_service=persistence_service
        )

        # Call shutdown and assert wait=True was passed to generation_service.stop
        controller.shutdown()
        generation_service.stop.assert_called_with(wait=True)

        # Reset mock and test stop_generation
        generation_service.stop.reset_mock()
        controller.stop_generation()
        generation_service.stop.assert_called_with(wait=True)

    def test_progress_tracker_reset_completed_count(self):
        """Verifies ProgressTracker.reset_completed_count updates progress counters correctly."""
        tracker = ProgressTracker(total_chunks=100)
        tracker.start(completed_chunks=100)
        self.assertEqual(tracker.get_completed_count(), 100)
        self.assertEqual(tracker.get_progress_percentage(), 100.0)

        # Reset completed count when 10 chunks need retry
        tracker.reset_completed_count(90)
        self.assertEqual(tracker.get_completed_count(), 90)
        self.assertEqual(tracker.get_progress_percentage(), 90.0)

    def test_audio_assembler_ffmpeg_command_standardizes_audio_format(self):
        """Verifies ffmpeg command includes -ar 24000 and -ac 1 parameters when re-encoding."""
        assembler = AudioAssembler()
        cmd = assembler._build_ffmpeg_command(
            list_path=Path("/tmp/concat.txt"),
            output_file_path=Path("/tmp/output.mp3"),
            speed=1.0,
            copy_codec=False
        )
        self.assertIn("-ar", cmd)
        self.assertIn("24000", cmd)
        self.assertIn("-ac", cmd)
        self.assertIn("1", cmd)

    def test_application_controller_get_current_task(self):
        """Verifies ApplicationController.get_current_task public method returns queue current task."""
        queue_service = MagicMock()
        mock_task = MagicMock(spec=GenerationTask)
        queue_service.get_current_task.return_value = mock_task

        controller = ApplicationController(
            queue_service=queue_service,
            text_processor=MagicMock(),
            text_chunker=MagicMock(),
            file_manager=MagicMock(),
            generation_service=MagicMock(),
            assembly_service=MagicMock(),
            persistence_service=MagicMock()
        )

        current = controller.get_current_task()
        self.assertEqual(current, mock_task)


if __name__ == "__main__":
    unittest.main()
