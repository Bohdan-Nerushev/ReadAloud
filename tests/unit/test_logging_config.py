import unittest
import logging
import threading
import tempfile
import shutil
from pathlib import Path
from src.infrastructure.logging_config import (
    set_correlation_id,
    get_correlation_id,
    CorrelationIdFilter,
    PiiMaskingFilter,
    setup_logging
)


class TestLoggingConfig(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_correlation_id_thread_isolation(self):
        """Verifies correlation ID is stored per-thread and default is N/A."""
        set_correlation_id("thread-main-123")
        self.assertEqual(get_correlation_id(), "thread-main-123")

        child_id = []

        def worker():
            child_id.append(get_correlation_id())
            set_correlation_id("thread-child-456")
            child_id.append(get_correlation_id())

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        # Child thread initial ID should be N/A, then thread-child-456
        self.assertEqual(child_id[0], "N/A")
        self.assertEqual(child_id[1], "thread-child-456")
        # Main thread ID should remain unchanged
        self.assertEqual(get_correlation_id(), "thread-main-123")

    def test_pii_masking_filter(self):
        """Verifies PiiMaskingFilter replaces home directory path with ~."""
        mask_filter = PiiMaskingFilter()
        home = str(Path.home())
        
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg=f"File saved to {home}/documents/audio.mp3",
            args=(),
            exc_info=None
        )
        mask_filter.filter(record)
        self.assertNotIn(home, record.msg)
        self.assertIn("~/documents/audio.mp3", record.msg)

    def test_setup_logging_creates_handler_and_file(self):
        """Verifies setup_logging configures root logger and creates log file."""
        log_dir = Path(self.temp_dir) / "test_logs"
        setup_logging(log_dir=str(log_dir))
        
        root_logger = logging.getLogger()
        self.assertTrue(root_logger.hasHandlers())
        
        logging.info("Test log entry")
        log_file = log_dir / "app.log"
        self.assertTrue(log_file.exists())


if __name__ == "__main__":
    unittest.main()
