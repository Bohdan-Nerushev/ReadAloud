"""Shared retry allowance for one generation session."""

import threading

MIN_RETRY_BUDGET = 50
RETRIES_PER_CHUNK = 3


class RetryBudget:
    """
    Caps the total number of retries of a whole generation, across all chunks.

    Per-chunk retry limits alone let a dead backend burn ``chunks x retries x delay``
    before the user sees an error. Once the budget is spent, chunks fail on their
    first error instead of retrying. Thread-safe.
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(0, int(limit))
        self._used = 0
        self._lock = threading.Lock()

    @classmethod
    def for_chunks(cls, chunk_count: int) -> "RetryBudget":
        """Budget scaled to the size of the job (never below ``MIN_RETRY_BUDGET``)."""
        return cls(max(MIN_RETRY_BUDGET, RETRIES_PER_CHUNK * max(0, chunk_count)))

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def used(self) -> int:
        return self._used

    @property
    def exhausted(self) -> bool:
        return self._used >= self._limit

    def try_consume(self) -> bool:
        """Takes one retry. Returns False (and takes nothing) when the budget is spent."""
        with self._lock:
            if self._used >= self._limit:
                return False
            self._used += 1
            return True
