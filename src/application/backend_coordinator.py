"""Lifecycle of the local TTS engines (Piper container, XTTS model) used by the controller."""

import logging
import threading
from typing import Any, Callable, Dict, Optional, Set

from src.domain.models import TtsBackend

ReleaseRunner = Callable[[Callable[[], None]], None]


class LocalBackend:
    """Strategy describing how one local engine is prepared and released."""

    backend: TtsBackend
    label: str
    # True if the engine is only released when this app run actually started it.
    release_only_if_used: bool = False

    def __init__(self, service: Any) -> None:
        self.service = service

    def ensure_ready(self, language: str, gender: str) -> Any:
        return self.service.ensure_ready(language, gender)

    def holds_resources(self) -> bool:
        """True if there is something to release right now."""
        return True

    def release(self) -> None:
        raise NotImplementedError

    def log_release(self) -> None:
        """Logs the release; overridden for engine-specific wording."""


class PiperBackend(LocalBackend):
    backend = TtsBackend.PIPER
    label = "Piper (Docker)"
    release_only_if_used = True  # Docker is never touched for Edge/XTTS-only sessions

    def release(self) -> None:
        logging.info("Stopping Piper container...")
        self.service.stop_container()


class XttsBackend(LocalBackend):
    backend = TtsBackend.XTTS
    label = "XTTS-v2"

    def holds_resources(self) -> bool:
        return bool(self.service.is_model_loaded())

    def release(self) -> None:
        logging.info("Unloading XTTS model from VRAM...")
        self.service.unload_model()


_STRATEGIES = {TtsBackend.PIPER: PiperBackend, TtsBackend.XTTS: XttsBackend}


class BackendCoordinator:
    """
    Owns readiness checks and release of local engines so the controller never
    branches on the engine type.

    * ``prepare_callable`` builds the blocking readiness routine (serialised by one lock).
    * A token identifies the latest preparation; stale completions are ignored.
    * ``release_idle`` frees engines no pending task needs; ``shutdown`` frees all at exit.
    """

    def __init__(self, piper_service: Any = None, xtts_service: Any = None) -> None:
        self._backends: Dict[TtsBackend, LocalBackend] = {}
        self.set_service(TtsBackend.PIPER, piper_service)
        self.set_service(TtsBackend.XTTS, xtts_service)
        self.lock = threading.Lock()
        self._token = 0
        # Engines a started/queued task needs; read by the release thread so it never
        # stops an engine that a new task has just begun to use.
        self.needed: Set[TtsBackend] = set()
        self.used: Set[TtsBackend] = set()

    # -- services ------------------------------------------------------

    def set_service(self, backend: TtsBackend, service: Any) -> None:
        if service is None:
            self._backends.pop(backend, None)
        else:
            self._backends[backend] = _STRATEGIES[backend](service)

    def service(self, backend: TtsBackend) -> Any:
        strategy = self._backends.get(backend)
        return strategy.service if strategy else None

    def label(self, backend: TtsBackend) -> str:
        strategy = self._backends.get(backend)
        return strategy.label if strategy else backend.value

    # -- preparation ---------------------------------------------------

    @property
    def token(self) -> int:
        return self._token

    def invalidate(self) -> None:
        """Makes any in-flight preparation ignore its result."""
        self._token += 1

    def is_current(self, token: int) -> bool:
        return token == self._token

    def begin(self, backend: TtsBackend) -> int:
        """Registers a new preparation for *backend* and returns its token."""
        self._token += 1
        self.needed.add(backend)
        self.used.add(backend)
        return self._token

    def prepare_callable(self, backend: TtsBackend, language: str, gender: str) -> Optional[Callable[[], Any]]:
        """Blocking readiness routine, or None if the backend needs no preparation."""
        strategy = self._backends.get(backend)
        if strategy is None:
            return None

        def prepare() -> Any:
            with self.lock:
                return strategy.ensure_ready(language, gender)

        return prepare

    # -- release -------------------------------------------------------

    def release_idle(self, has_unfinished: Callable[[TtsBackend], bool], runner: ReleaseRunner) -> None:
        """
        Frees engines that no remaining task needs. The decision is made on the caller's
        thread; the slow part runs through *runner* (background in production).
        """
        to_release = [
            s for b, s in self._backends.items()
            if (b in self.used or not s.release_only_if_used) and not has_unfinished(b)
        ]
        for strategy in to_release:
            self.used.discard(strategy.backend)
            self.needed.discard(strategy.backend)
        if not to_release:
            return

        def release() -> None:
            with self.lock:
                for strategy in to_release:
                    if strategy.backend in self.needed:
                        continue
                    self._release_one(strategy, only_if_holding=not strategy.release_only_if_used)

        runner(release)

    def shutdown(self) -> None:
        """Releases every engine at application exit (errors are logged, never raised)."""
        for strategy in list(self._backends.values()):
            self._release_one(strategy, only_if_holding=not strategy.release_only_if_used)

    @staticmethod
    def _release_one(strategy: LocalBackend, only_if_holding: bool) -> None:
        try:
            if only_if_holding and not strategy.holds_resources():
                return
            strategy.release()
        except Exception as exc:
            logging.warning("Releasing %s failed: %s", strategy.label, exc)
