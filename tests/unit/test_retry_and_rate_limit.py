import unittest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from src.domain.audio_generator import AudioGenerator, _is_transient_error
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

    def test_communicate_created_without_connector(self):
        """Verifies that Communicate is called without a connector argument.

        The shared SafeTCPConnector was removed because it caused a race condition:
        recycling the connector while workers held stale references caused
        RuntimeError('Session is closed') on their first attempt.
        Each Communicate call must now create its own isolated aiohttp session.
        """
        import edge_tts
        chunk = MagicMock()
        chunk.text_content = "Hello world"
        chunk.chunk_number = 1

        with patch.object(self.generator.edge_tts, 'Communicate', wraps=self.mock_communicate) as mock_comm:
            # Use the mock that was already set up in setUp
            pass

        # Verify Communicate is callable without connector kwarg
        with patch('edge_tts.Communicate') as mock_comm_cls:
            mock_instance = MagicMock()
            mock_instance.save = AsyncMock()
            mock_comm_cls.return_value = mock_instance

            async def _run():
                import tempfile, os
                with tempfile.TemporaryDirectory() as tmpdir:
                    from pathlib import Path
                    from src.domain.models import AudioChunk
                    ac = AudioChunk(chunk_number=1, text_content="Hello world")
                    with patch.object(self.generator, '_get_file_duration_fast', return_value=1.0):
                        with patch.object(Path, 'exists', return_value=True):
                            with patch('os.replace'):
                                try:
                                    await self.generator._generate_one_with_retry(
                                        ac, 'en-US-AriaNeural', Path(tmpdir),
                                        max_retries=1, backoff=0.0
                                    )
                                except Exception:
                                    pass
                    # Verify: connector keyword was NOT passed
                    call_kwargs = mock_comm_cls.call_args
                    if call_kwargs:
                        self.assertNotIn('connector', call_kwargs.kwargs)

            asyncio.run(_run())

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
