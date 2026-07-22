"""
Network connectivity and WLAN recovery management.

This module provides network health checking and automatic WLAN interface
toggling to recover from network disconnects during TTS processing.
"""

import socket
import time
import subprocess
import logging
import threading
from typing import Optional, List, Tuple


class NetworkManager:
    """
    Service responsible for checking network connectivity and attempting
    WLAN recovery when connection to external servers fails.
    """

    DEFAULT_TEST_TARGETS: List[Tuple[str, int]] = [
        ("8.8.8.8", 53),                # Google Primary DNS
        ("1.1.1.1", 53),                # Cloudflare DNS
        ("speech.platform.bing.com", 443) # Edge TTS / Azure Speech Endpoint
    ]

    def __init__(self, test_targets: Optional[List[Tuple[str, int]]] = None) -> None:
        """Initialize the NetworkManager."""
        self._test_targets = test_targets or self.DEFAULT_TEST_TARGETS
        self._lock = threading.Lock()

    def is_connected(self, timeout: float = 3.0) -> bool:
        """
        Checks if the system has an active internet connection by attempting
        to connect to reliable test endpoints.

        Args:
            timeout: Timeout in seconds for each socket connection attempt.

        Returns:
            True if at least one endpoint is reachable, False otherwise.
        """
        for host, port in self._test_targets:
            try:
                sock = socket.create_connection((host, port), timeout=timeout)
                sock.close()
                return True
            except (OSError, socket.error):
                continue
        return False

    def toggle_wlan(self) -> bool:
        """
        Attempts to restart the WLAN interface on Linux using standard tools
        (nmcli, rfkill).

        Returns:
            True if a toggle command executed successfully, False otherwise.
        """
        logging.info("Network down detected. Attempting to toggle WLAN interface...")

        # Method 1: NetworkManager cli (nmcli)
        try:
            res_off = subprocess.run(
                ["nmcli", "radio", "wifi", "off"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if res_off.returncode == 0:
                time.sleep(2.0)
                res_on = subprocess.run(
                    ["nmcli", "radio", "wifi", "on"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if res_on.returncode == 0:
                    logging.info("WLAN interface toggled successfully using nmcli.")
                    return True
        except Exception as e:
            logging.debug(f"nmcli toggle attempt failed: {e}")

        # Method 2: rfkill
        try:
            res_block = subprocess.run(
                ["rfkill", "block", "wlan"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if res_block.returncode == 0:
                time.sleep(2.0)
                res_unblock = subprocess.run(
                    ["rfkill", "unblock", "wlan"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if res_unblock.returncode == 0:
                    logging.info("WLAN interface toggled successfully using rfkill.")
                    return True
        except Exception as e:
            logging.debug(f"rfkill toggle attempt failed: {e}")

        logging.warning("WLAN toggle command not supported or failed due to system permissions/tool absence.")
        return False

    def wait_for_network(
            self,
            max_wait_seconds: float = 300.0,
            check_interval: float = 5.0,
            auto_toggle_wlan: bool = True
    ) -> bool:
        """
        Waits until network connection is restored or until max_wait_seconds expires.

        Periodically checks connectivity, logging status updates, and attempts
        WLAN restart after initial failed attempts.

        Args:
            max_wait_seconds: Maximum duration in seconds to wait for network.
            check_interval: Delay in seconds between connection checks.
            auto_toggle_wlan: If True, attempts to toggle WLAN if down > 15s.

        Returns:
            True if connection restored, False if timeout exceeded.
        """
        if self.is_connected():
            return True

        logging.warning("Internet connection lost. Entering network recovery wait state...")
        start_time = time.time()
        toggled_wlan = False

        while (time.time() - start_time) < max_wait_seconds:
            elapsed = time.time() - start_time

            # Attempt WLAN toggle once after 15 seconds of disconnection if enabled
            if auto_toggle_wlan and not toggled_wlan and elapsed >= 15.0:
                toggled_wlan = True
                self.toggle_wlan()

            time.sleep(check_interval)

            if self.is_connected():
                total_wait = time.time() - start_time
                logging.info(f"Network connection restored successfully after {total_wait:.1f}s!")
                return True

            logging.warning(
                f"Still waiting for network connection... "
                f"({int(elapsed)}s / {int(max_wait_seconds)}s elapsed)"
            )

        logging.error(f"Network recovery timed out after {max_wait_seconds} seconds.")
        return False
