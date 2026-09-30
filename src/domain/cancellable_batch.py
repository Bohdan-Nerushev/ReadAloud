"""Shared cancellation support for generators that run batches on a private asyncio loop."""

import asyncio
import concurrent.futures
import threading
from typing import Any, Optional, Set


class CancellableBatchMixin:
    """
    Lets another thread (e.g. the GUI) abort in-flight batches promptly.

    Cancelling the concurrent future cancels the coroutine on the event loop, so
    network I/O and back-off sleeps stop immediately instead of running to the end.
    Cancellation is not sticky: batches started afterwards run normally.
    """

    last_error: Optional[str] = None

    def _init_cancellation(self) -> None:
        self._active_futures: Set["concurrent.futures.Future[Any]"] = set()
        self._futures_lock = threading.Lock()

    def cancel(self) -> None:
        """Cancels every batch that is currently running."""
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
