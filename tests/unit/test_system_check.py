import unittest
from unittest.mock import patch
from src.infrastructure.system_check import check_dependencies


class TestSystemCheck(unittest.TestCase):

    @patch("shutil.which")
    def test_check_dependencies_all_present(self, mock_which):
        """Verifies check_dependencies returns True when ffmpeg and ffprobe exist."""
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"
        success, missing = check_dependencies()
        self.assertTrue(success)
        self.assertEqual(len(missing), 0)

    @patch("shutil.which")
    def test_check_dependencies_missing_ffmpeg(self, mock_which):
        """Verifies check_dependencies returns False and lists missing tools when ffmpeg is missing."""
        def which_side_effect(cmd):
            if cmd == "ffmpeg":
                return None
            return f"/usr/bin/{cmd}"

        mock_which.side_effect = which_side_effect
        success, missing = check_dependencies()
        self.assertFalse(success)
        self.assertIn("ffmpeg", missing)
        self.assertEqual(len(missing), 1)

    @patch("shutil.which")
    def test_check_dependencies_all_missing(self, mock_which):
        """Verifies check_dependencies returns False when all dependencies are missing."""
        mock_which.return_value = None
        success, missing = check_dependencies()
        self.assertFalse(success)
        self.assertEqual(set(missing), {"ffmpeg", "ffprobe"})


if __name__ == "__main__":
    unittest.main()
