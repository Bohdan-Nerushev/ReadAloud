"""
Unit tests for AudioGenerator background loop thread lifecycle and auto-recovery.
"""

import unittest
import time
from src.domain.audio_generator import AudioGenerator


class TestAudioGeneratorLoopLifecycle(unittest.TestCase):

    def test_ensure_loop_running_auto_revives(self):
        """Verify that ensure_loop_running revives background loop if closed."""
        generator = AudioGenerator()
        try:
            self.assertTrue(generator._loop.is_running())
            
            # Close the loop manually to simulate unexpected closure or task stop
            generator.close()
            time.sleep(0.2)
            self.assertFalse(generator._loop.is_running())

            # ensure_loop_running should revive the background loop
            generator.ensure_loop_running()
            self.assertTrue(generator._loop.is_running())
            self.assertTrue(generator._loop_thread.is_alive())
        finally:
            generator.close()


if __name__ == "__main__":
    unittest.main()
