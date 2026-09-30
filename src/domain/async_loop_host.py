"""Background asyncio event loop shared by the Edge and Piper generators."""

import asyncio
import threading
from typing import Optional

LOOP_START_TIMEOUT_SECONDS = 10.0
LOOP_JOIN_TIMEOUT_SECONDS = 2.0


class AsyncLoopHostMixin:
    """
    Runs one asyncio loop in a daemon thread and restarts it if it died.

    Subclasses set ``_LOOP_THREAD_NAME`` and may override ``_on_loop_started`` to create
    loop-bound primitives (semaphores, locks) inside the new loop's thread.
    """

    _LOOP_THREAD_NAME = "Generator-AsyncLoop"

    def _init_loop_host(self) -> None:
        self._init_lock = threading.Lock()
        self._loop_ready = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

    def _on_loop_started(self) -> None:
        """Hook called in the loop thread before the loop starts running."""

    def ensure_loop_running(self) -> None:
        """Ensures the background asyncio event loop thread is active and running."""
        with self._init_lock:
            if (
                self._loop is None
                or not self._loop.is_running()
                or self._loop_thread is None
                or not self._loop_thread.is_alive()
            ):
                self._loop_ready.clear()
                self._loop = asyncio.new_event_loop()
                self._loop_thread = threading.Thread(
                    target=self._start_background_loop,
                    args=(self._loop,),
                    daemon=True,
                    name=self._LOOP_THREAD_NAME,
                )
                self._loop_thread.start()
                if not self._loop_ready.wait(timeout=LOOP_START_TIMEOUT_SECONDS):
                    raise RuntimeError(
                        f"{type(self).__name__}: background asyncio loop did not start "
                        f"within {LOOP_START_TIMEOUT_SECONDS:g} seconds"
                    )

    def _start_background_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Runs the asyncio event loop in a dedicated thread."""
        asyncio.set_event_loop(loop)
        self._on_loop_started()
        self._loop_ready.set()
        loop.run_forever()

    def close(self) -> None:
        """Stops and closes the background asyncio event loop."""
        loop = getattr(self, "_loop", None)
        if not loop:
            return
        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        thread = getattr(self, "_loop_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=LOOP_JOIN_TIMEOUT_SECONDS)
        try:
            loop.close()
        except Exception:
            pass
