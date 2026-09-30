"""Shared cancellation support for generators that run batches on a private asyncio loop."""

import asyncio
import concurrent.futures
import threading
import time
from typing import Any, Optional, Set


class PausableMixin:
    """
    Cooperative pause shared by all generators.

    ``pause()`` only raises a flag. Workers call ``_await_resume`` (async) or
    ``_wait_while_paused`` (sync) before they start the next chunk or retry attempt,
    so a batch that is already running stops after the chunk it is on, not after the
    whole batch. Cancelling (or resuming) releases every waiter.
    """

    _PAUSE_POLL_SECONDS = 0.1

    def _init_pause(self) -> None:
        self._paused = threading.Event()

    def pause(self) -> None:
        """Holds back every chunk that has not started yet."""
        self._paused.set()

    def resume(self) -> None:
        """Lets held-back chunks continue."""
        self._paused.clear()

    def is_paused(self) -> bool:
        return self._paused.is_set()

    async def _await_resume(self) -> None:
        """Async gate: returns once the generator is not paused (cancellation propagates)."""
        while self._paused.is_set():
            await asyncio.sleep(self._PAUSE_POLL_SECONDS)

    def _wait_while_paused(self, should_abort: Any = None) -> None:
        """Sync gate: blocks while paused; returns early when ``should_abort()`` is true."""
        while self._paused.is_set():
            if should_abort is not None and should_abort():
                return
            time.sleep(self._PAUSE_POLL_SECONDS)


class CancellableBatchMixin(PausableMixin):
    """
    Lets another thread (e.g. the GUI) abort in-flight batches promptly.

    Cancelling the concurrent future cancels the coroutine on the event loop, so
    network I/O and back-off sleeps stop immediately instead of running to the end.
    Cancellation is not sticky: batches started afterwards run normally.
    """

    last_error: Optional[str] = None

    def _init_cancellation(self) -> None:
        self._init_pause()
        self._active_futures: Set["concurrent.futures.Future[Any]"] = set()
        self._futures_lock = threading.Lock()

    def cancel(self) -> None:
        """Cancels every batch that is currently running."""
        self._paused.clear()
        with self._futures_lock:
            futures = list(self._active_futures)
        for future in futures:
            future.cancel()

    def _run_batch_coroutine(self, coro: Any, loop: asyncio.AbstractEventLoop, size: int) -> list:
        """
        Runs *coro* on *loop* and waits. Returns ``[None] * size`` if the batch was cancelled
        (all chunks count as not generated); other errors propagate.
        """
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        with self._futures_lock:
            self._active_futures.add(future)
        try:
            return future.result(timeout=None)
        except (concurrent.futures.CancelledError, asyncio.CancelledError):
            return [None] * size
        except BaseException:
            future.cancel()
            raise
        finally:
            with self._futures_lock:
                self._active_futures.discard(future)
