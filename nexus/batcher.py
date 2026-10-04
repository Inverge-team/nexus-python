"""Background batching for high-volume telemetry (events and logs).

Analytics should never sit on your request's critical path. ``track()`` and
``log.info()`` append to an in-memory buffer; a daemon thread ships it when the
buffer reaches ``max_batch`` or ``flush_interval`` elapses, grouping items that
share an identity/context into one request — which is exactly how the ingest
endpoints want them.

The buffer is bounded (``max_queue``): under a flood the SDK drops the newest
items and counts them, rather than growing until the process dies.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ._logging import logger

__all__ = ["BatchQueue"]

Sender = Callable[[List[Dict[str, Any]], Dict[str, Any]], None]


def _context_key(context: Mapping[str, Any]) -> str:
    """A stable grouping key for a context dict."""
    try:
        return json.dumps(context, sort_keys=True, default=str)
    except Exception:  # pragma: no cover - defensive
        return repr(sorted(context.items()))


class BatchQueue:
    """A bounded, context-grouped, time-and-size triggered send buffer."""

    def __init__(
        self,
        name: str,
        sender: Sender,
        *,
        max_batch: int = 50,
        interval: float = 5.0,
        max_queue: int = 10000,
    ) -> None:
        self.name = name
        self._sender = sender
        self._max_batch = max(1, int(max_batch))
        self._interval = max(0.1, float(interval))
        self._max_queue = max(self._max_batch, int(max_queue))

        self._pid = os.getpid()
        self._items: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        self._cond = threading.Condition(threading.Lock())
        self._thread: Optional[threading.Thread] = None
        self._stopping = False
        self._flush_now = False
        self.dropped = 0

        atexit.register(self._atexit)

    # ── public API ───────────────────────────────────────────────────────────

    def add(self, item: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        """Queue one item. Returns immediately; delivery happens off-thread."""
        self._guard_fork()
        with self._cond:
            if len(self._items) >= self._max_queue:
                self.dropped += 1
                if self.dropped in (1, 10, 100) or self.dropped % 1000 == 0:
                    logger.warning(
                        "nexus: %s buffer full (%d items) — dropped %d so far",
                        self.name,
                        self._max_queue,
                        self.dropped,
                    )
                return
            self._items.append((_context_key(context), dict(context), dict(item)))
            ready = len(self._items) >= self._max_batch
            if ready:
                self._flush_now = True
                self._cond.notify()
        self._ensure_worker()

    def flush(self, timeout: Optional[float] = None) -> None:
        """Send everything buffered right now, on the calling thread."""
        self._guard_fork()
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._cond:
                if not self._items:
                    return
                batch = self._items
                self._items = []
            self._send_grouped(batch)
            if deadline is not None and time.monotonic() >= deadline:
                return

    def close(self, timeout: float = 3.0) -> None:
        """Stop the worker and flush what is left. Idempotent."""
        with self._cond:
            if self._stopping:
                return
            self._stopping = True
            self._cond.notify_all()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self.flush(timeout=timeout)

    @property
    def pending(self) -> int:
        """How many items are waiting to be sent."""
        with self._cond:
            return len(self._items)

    # ── internals ────────────────────────────────────────────────────────────

    def _guard_fork(self) -> None:
        """After ``fork()`` the child inherits the buffer but not the thread.

        Gunicorn/uWSGI prefork workers hit this whenever a client is built in
        the master process. Reset the child's state so it starts its own worker
        with an empty buffer instead of silently never sending anything (and
        re-sending whatever the parent had queued).
        """
        pid = os.getpid()
        if pid == self._pid:
            return
        self._pid = pid
        self._cond = threading.Condition(threading.Lock())
        self._items = []
        self._thread = None
        self._stopping = False
        self._flush_now = False

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stopping:
                return
            thread = threading.Thread(
                target=self._run, name="nexus-{0}-flush".format(self.name), daemon=True
            )
            self._thread = thread
        thread.start()

    def _run(self) -> None:
        while True:
            with self._cond:
                # Sleep until the interval elapses, or until add() signals that
                # the buffer hit max_batch, or until close() wakes us.
                if not self._stopping and not self._flush_now:
                    self._cond.wait(self._interval)
                self._flush_now = False
                stopping = self._stopping
                batch = self._items
                self._items = []
            if batch:
                self._send_grouped(batch)
            if stopping:
                return

    def _send_grouped(self, batch: List[Tuple[str, Dict[str, Any], Dict[str, Any]]]) -> None:
        groups: Dict[str, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}
        for key, context, item in batch:
            bucket = groups.get(key)
            if bucket is None:
                groups[key] = (context, [item])
            else:
                bucket[1].append(item)

        for context, items in groups.values():
            for start in range(0, len(items), self._max_batch):
                chunk = items[start : start + self._max_batch]
                try:
                    self._sender(chunk, context)
                except Exception as exc:  # delivery must never break the app
                    logger.warning("nexus: %s delivery failed (%d items): %s", self.name, len(chunk), exc)

    def _atexit(self) -> None:  # pragma: no cover - interpreter teardown
        try:
            self.close(timeout=2.0)
        except Exception:
            pass
