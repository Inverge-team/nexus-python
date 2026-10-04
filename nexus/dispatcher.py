"""Where a request actually goes when a resource method is called.

* :class:`DirectDispatcher` — send now, return the decoded body, raise on error.
  The default.
* :class:`BackgroundDispatcher` — hand the request to a worker thread and return
  ``None`` immediately. Use it when you want *every* call fire-and-forget::

      bg = nexus.background()          # a client clone, same config
      bg.push.send_to_user("u_1", title="Welcome")   # returns None, never blocks

  Result-returning calls (flag evaluation, Remote Config) make no sense on a
  background dispatcher — they can't return anything.

Swap in your own (Celery, RQ, Dramatiq, SQS) by implementing
:class:`Dispatcher`; :mod:`nexus.integrations.celery` ships one.
"""

from __future__ import annotations

import atexit
import os
import queue
import threading
from typing import Any, Optional

from ._engine import HttpEngine
from ._logging import logger
from ._payloads import Request
from .exceptions import NexusError

__all__ = ["Dispatcher", "DirectDispatcher", "BackgroundDispatcher"]


class Dispatcher:
    """Interface: deliver a request, optionally returning the decoded body."""

    def dispatch(self, request: Request) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def flush(self, timeout: Optional[float] = None) -> None:
        """Wait for in-flight deliveries. No-op for synchronous dispatchers."""

    def close(self, timeout: float = 3.0) -> None:
        """Release resources."""


class DirectDispatcher(Dispatcher):
    """Send synchronously through the engine."""

    def __init__(self, engine: HttpEngine) -> None:
        self.engine = engine

    def dispatch(self, request: Request) -> Any:
        return self.engine.execute(request)

    def close(self, timeout: float = 3.0) -> None:
        self.engine.close()


class BackgroundDispatcher(Dispatcher):
    """Deliver on worker threads; never raise into the caller.

    Failures are logged to the ``nexus`` logger. The queue is bounded, so a
    backend outage costs memory you chose, not all of it.
    """

    def __init__(self, engine: HttpEngine, *, workers: int = 1, max_queue: int = 5000) -> None:
        self.engine = engine
        self._workers = max(1, int(workers))
        self._queue: "queue.Queue[Optional[Request]]" = queue.Queue(maxsize=max(1, int(max_queue)))
        self._threads: list = []
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self._closed = False
        self.dropped = 0
        atexit.register(self._atexit)

    def dispatch(self, request: Request) -> Any:
        self._guard_fork()
        self._ensure_workers()
        try:
            self._queue.put_nowait(request)
        except queue.Full:
            self.dropped += 1
            logger.warning(
                "nexus: background queue full — dropped %s %s (%d total)",
                request.method,
                request.path,
                self.dropped,
            )
        return None

    def flush(self, timeout: Optional[float] = None) -> None:
        """Block until the queue drains (best effort)."""
        if not self._threads:
            return
        if timeout is None:
            self._queue.join()
            return
        # Queue.join() has no timeout; poll instead so a slow backend can't hang
        # a web worker's shutdown hook forever.
        import time

        deadline = time.monotonic() + timeout
        while not self._queue.empty() and time.monotonic() < deadline:
            time.sleep(0.02)

    def close(self, timeout: float = 3.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            threads = list(self._threads)
        for _ in threads:
            try:
                self._queue.put_nowait(None)
            except queue.Full:  # pragma: no cover - defensive
                pass
        for thread in threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=timeout)
        self.engine.close()

    # ── internals ────────────────────────────────────────────────────────────

    def _guard_fork(self) -> None:
        pid = os.getpid()
        if pid == self._pid:
            return
        # See BatchQueue._guard_fork — threads don't survive fork().
        self._pid = pid
        self._queue = queue.Queue(maxsize=self._queue.maxsize)
        self._threads = []
        self._closed = False

    def _ensure_workers(self) -> None:
        if len(self._threads) >= self._workers:
            return
        with self._lock:
            if self._closed or len(self._threads) >= self._workers:
                return
            while len(self._threads) < self._workers:
                thread = threading.Thread(
                    target=self._run, name="nexus-dispatch-{0}".format(len(self._threads)), daemon=True
                )
                self._threads.append(thread)
                thread.start()

    def _run(self) -> None:
        while True:
            request = self._queue.get()
            try:
                if request is None:
                    return
                try:
                    self.engine.execute(request)
                except NexusError as exc:
                    logger.warning("nexus: %s %s failed: %s", request.method, request.path, exc)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("nexus: unexpected delivery error: %s", exc)
            finally:
                self._queue.task_done()

    def _atexit(self) -> None:  # pragma: no cover - interpreter teardown
        try:
            self.close(timeout=2.0)
        except Exception:
            pass
