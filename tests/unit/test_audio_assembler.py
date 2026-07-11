import unittest
import sys
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.domain.audio_assembler import AudioAssembler

class TestAudioAssembler(unittest.TestCase):
    def setUp(self):
        self.assembler = AudioAssembler()
        self.mock_popen = patch('subprocess.Popen').start()
        self.mock_run = patch('subprocess.run').start()
        
        # Setup mock process
        self.process_instance = MagicMock()
        self.process_instance.returncode = 0
        self.process_instance.poll.return_value = 0
        self.process_instance.stderr.readline.return_value = ""
        self.mock_popen.return_value = self.process_instance
        
        # Setup mock ffprobe result
        self.ffprobe_instance = MagicMock()
        self.ffprobe_instance.stdout = "10.0"
        self.mock_run.return_value = self.ffprobe_instance

    def tearDown(self):
        patch.stopall()

    def test_assemble_audio_creates_concat_file(self):
        """test_assemble_audio_creates_concat_file: Verifies creation of the text file list for concatenation."""
        audio_files = ["/tmp/1.mp3", "/tmp/2.mp3"]
        output_path = "/tmp/output.mp3"
        
        # We need to mock open to verify content
        with patch("builtins.open", mock_open()) as mocked_file:
            # We also need to avoid path issues with Path().unlink() in finally block
            with patch("src.domain.audio_assembler.Path.exists", return_value=True):
                with patch("src.domain.audio_assembler.Path.unlink"):
                    self.assembler.assemble_audio(audio_files, output_path)
                    
                    # Verify open was called for concat file
                    # The filename is random (uuid), so we check if any call matches
                    mocked_file.assert_any_call(unittest.mock.ANY, 'w', encoding='utf-8')
                    
                    # Verify content written
                    handle = mocked_file()
                    # Check if 'file' keyword is in any write call
                    calls = [call.args[0] for call in handle.write.call_args_list]
                    self.assertTrue(any("file '/tmp/1.mp3'" in c for c in calls))

    def test_assemble_audio_ffmpeg_command_structure(self):
        """test_assemble_audio_ffmpeg_command_structure: Verifies correctness of the ffmpeg command structure."""
        audio_files = ["1.mp3"]
        output_path = "out.mp3"
        
        # Mock everything to just get to _execute_ffmpeg
        with patch.object(self.assembler, '_calculate_total_duration', return_value=1.0):
            with patch.object(self.assembler, '_create_concat_list'):
                self.assembler.assemble_audio(audio_files, output_path)
                
                cmd = self.mock_popen.call_args[0][0]
                self.assertEqual(cmd[0], 'ffmpeg')
                self.assertIn('-f', cmd)
                self.assertIn('concat', cmd)
                self.assertIn('out.mp3', cmd)

    def test_assemble_audio_speed_adjustment(self):
        """test_assemble_audio_speed_adjustment: Verifies addition of the atempo audio filter for speed adjustment."""
        audio_files = ["1.mp3"]
        output_path = "out.mp3"
        
        with patch.object(self.assembler, '_calculate_total_duration', return_value=1.0):
            with patch.object(self.assembler, '_create_concat_list'):
                self.assembler.assemble_audio(audio_files, output_path, speed=1.5)
                
                cmd = self.mock_popen.call_args[0][0]
                self.assertIn('-filter:a', cmd)
                self.assertIn('atempo=1.5', cmd)

    def test_assemble_audio_empty_list(self):
        """test_assemble_audio_empty_list: Verifies ValueError is raised for an empty file list."""
        with self.assertRaises(ValueError):
            self.assembler.assemble_audio([], "out.mp3")

    def test_assemble_audio_ffmpeg_error(self):
        """test_assemble_audio_ffmpeg_error: Verifies an exception is raised when ffmpeg fails with an error."""
        self.process_instance.returncode = 1
        audio_files = ["1.mp3"]
        
        with patch.object(self.assembler, '_calculate_total_duration', return_value=1.0):
            with patch.object(self.assembler, '_create_concat_list'):
                with self.assertRaises(Exception) as cm:
                    self.assembler.assemble_audio(audio_files, "out.mp3")
                self.assertIn("ffmpeg exited with code 1", str(cm.exception))

    def test_stop_terminates_processes(self):
        """test_stop_terminates_processes: Verifies active ffmpeg processes are terminated on stop."""
        mock_p = MagicMock()
        self.assembler._active_processes.add(mock_p)
        
        self.assembler.stop()
        mock_p.terminate.assert_called_once()
        self.assertEqual(len(self.assembler._active_processes), 0)

if __name__ == '__main__':
    unittest.main()
