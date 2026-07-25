import unittest
import asyncio
import aiohttp
from unittest.mock import MagicMock, patch
from src.domain.models import TaskStatus
from src.domain.audio_generator import SafeTCPConnector
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


if __name__ == "__main__":
    unittest.main()
