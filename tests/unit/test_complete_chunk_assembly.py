import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

from src.application.services.assembly_service import AssemblyService
from src.domain.audio_assembler import AudioAssembler


class TestCompleteChunkAssembly(unittest.TestCase):
    def setUp(self):
        self.mock_assembler = MagicMock()
        self.service = AssemblyService(self.mock_assembler)

    def test_assemble_final_raises_error_on_missing_chunks(self):
        """Verifies assemble_final raises ValueError if any chunk file is missing or empty."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp3") as f1, tempfile.NamedTemporaryFile(suffix=".mp3") as f3:
            f1.write(b"audio_data")
            f1.flush()
            f3.write(b"audio_data")
            f3.flush()
            self.service.reset(total_chunks=3)
            chunk_files = [f1.name, None, f3.name]

            with self.assertRaises(ValueError) as ctx:
                self.service.assemble_final(Path("output.mp3"), chunk_files, 1.0, "test-corr-id")

            self.assertIn("1 chunk(s) missing or empty", str(ctx.exception))

    def test_submit_batch_by_index_aborts_on_missing_chunk(self):
        """Verifies submit_batch_by_index skips submitting assembly if a chunk is None or missing."""
        self.service.reset(total_chunks=3)
        files = ["file1.mp3", None, "file3.mp3"]

        with patch.object(self.service._executor, 'submit') as mock_submit:
            self.service.submit_batch_by_index(0, files, "/tmp", 1.0, "test-corr-id")
            mock_submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
