"""
Unit tests for NetworkManager.
"""

import unittest
from unittest.mock import patch, MagicMock
from src.infrastructure.network_manager import NetworkManager


class TestNetworkManager(unittest.TestCase):
    def setUp(self):
        self.net_manager = NetworkManager()

    @patch('socket.create_connection')
    def test_is_connected_success(self, mock_create_connection):
        mock_create_connection.return_value = MagicMock()
        self.assertTrue(self.net_manager.is_connected())

    @patch('socket.create_connection')
    def test_is_connected_failure(self, mock_create_connection):
        mock_create_connection.side_effect = OSError("No network")
        self.assertFalse(self.net_manager.is_connected())

    @patch('subprocess.run')
    def test_toggle_wlan_nmcli_success(self, mock_subprocess_run):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_subprocess_run.return_value = mock_res
        
        result = self.net_manager.toggle_wlan()
        self.assertTrue(result)
        self.assertGreaterEqual(mock_subprocess_run.call_count, 1)

    @patch.object(NetworkManager, 'is_connected')
    def test_wait_for_network_already_connected(self, mock_is_connected):
        mock_is_connected.return_value = True
        self.assertTrue(self.net_manager.wait_for_network(max_wait_seconds=1.0))


if __name__ == '__main__':
    unittest.main()
