"""Registry of child processes that must not outlive the application."""

import atexit
import logging
import subprocess
import threading
from typing import Set


class ProcessRegistry:
    """Thread-safe set of running subprocesses with a kill-all for interpreter exit."""

    def __init__(self) -> None:
        self._processes: Set[subprocess.Popen] = set()
        self._lock = threading.Lock()

    def add(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.add(process)

    def discard(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._processes.discard(process)

    def __len__(self) -> int:
        with self._lock:
            return len(self._processes)

    def kill_all(self) -> None:
        """Kills every registered process; failures are ignored."""
        with self._lock:
            processes = list(self._processes)
        if processes:
            logging.info(f"Cleaning up {len(processes)} active subprocesses...")
        for p in processes:
            try:
                p.kill()
            except Exception:
                pass


DEFAULT_PROCESS_REGISTRY = ProcessRegistry()
atexit.register(DEFAULT_PROCESS_REGISTRY.kill_all)
