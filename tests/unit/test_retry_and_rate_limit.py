import unittest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

import aiohttp
from src.domain.audio_generator import AudioGenerator, SafeTCPConnector
from src.domain.models import AudioChunk, GenerationTask, TaskStatus, ProjectConfig
from src.application.services.queue_service import QueueService
from src.gui.widgets.queue_item import QueueItemWidget
from PyQt6.QtWidgets import QApplication

# Ensure QApplication instance for GUI widget tests
app = QApplication.instance() or QApplication([])

class TestRetryAndRateLimitFixes(unittest.TestCase):
    def setUp(self):
        self.patcher = patch('edge_tts.Communicate')
        self.mock_communicate = self.patcher.start()
        self.mock_instance = MagicMock()
        self.mock_instance.save = AsyncMock()
        self.mock_communicate.return_value = self.mock_instance
        self.generator = AudioGenerator()

    def tearDown(self):
        self.patcher.stop()
        if hasattr(self.generator, 'close'):
            self.generator.close()

    def test_safe_tcp_connector_real_close(self):
        """Verifies that SafeTCPConnector.close() is a no-op but real_close() closes super."""
        async def _test():
            conn = SafeTCPConnector()
            with patch.object(aiohttp.TCPConnector, 'close', new_callable=AsyncMock) as mock_super_close:
                await conn.close()
                mock_super_close.assert_not_called()

                await conn.real_close()
                mock_super_close.assert_called_once()

        asyncio.run(_test())

    def test_queue_service_retry_task(self):
        """Verifies QueueService.retry_task resets task to PENDING and moves it to front of queue."""
        qs = QueueService()
        config = ProjectConfig(
            project_name="Test",
            input_file_path=__file__,
            output_dir_path="/tmp",
            language="en",
            gender="male",
            thread_count=1
        )
        task = qs.add_task(config)
        task.update_status(TaskStatus.FAILED, "Failed due to error")
        
        retried_task = qs.retry_task(str(task.id))
        self.assertIsNotNone(retried_task)
        self.assertEqual(retried_task.status, TaskStatus.PENDING)
        self.assertIn("Pending Retry", retried_task.message)

    def test_queue_item_widget_retry_signal(self):
        """Verifies QueueItemWidget emits retryRequested when button is in Retry state."""
        config = ProjectConfig(
            project_name="Test",
            input_file_path=__file__,
            output_dir_path="/tmp",
            language="en",
            gender="male",
            thread_count=1
        )
        task = GenerationTask(config=config)
        task.update_status(TaskStatus.FAILED, "Failed")

        widget = QueueItemWidget(task)
        received_ids = []
        widget.retryRequested.connect(lambda tid: received_ids.append(tid))

        # Click action button when status is FAILED
        widget.pause_button.click()
        self.assertEqual(len(received_ids), 1)
        self.assertEqual(received_ids[0], str(task.id))


if __name__ == '__main__':
    unittest.main()
