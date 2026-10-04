"""Asyncio batching for the async client — the event-loop twin of
:class:`nexus.batcher.BatchQueue`."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple

from .._logging import logger

__all__ = ["AsyncBatchQueue"]

Sender = Callable[[List[Dict[str, Any]], Dict[str, Any]], Awaitable[Any]]


def _context_key(context: Mapping[str, Any]) -> str:
    try:
        return json.dumps(context, sort_keys=True, default=str)
    except Exception:  # pragma: no cover - defensive
        return repr(sorted(context.items()))


class AsyncBatchQueue:
    """Buffers items and flushes them from a background asyncio task."""

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
        self._items: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        self._task: Optional["asyncio.Task[None]"] = None
        self._wake: Optional[asyncio.Event] = None
        self._closed = False
        self.dropped = 0

    def add(self, item: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        """Queue one item. Non-blocking and safe to call from any coroutine."""
        if len(self._items) >= self._max_queue:
            self.dropped += 1
            if self.dropped in (1, 10, 100) or self.dropped % 1000 == 0:
                logger.warning("nexus: %s buffer full — dropped %d", self.name, self.dropped)
            return
        self._items.append((_context_key(context), dict(context), dict(item)))
        self._ensure_task()
        if len(self._items) >= self._max_batch and self._wake is not None:
            self._wake.set()

    async def flush(self) -> None:
        """Send everything buffered right now."""
        while self._items:
            batch = self._items
            self._items = []
            await self._send_grouped(batch)

    async def aclose(self) -> None:
        """Stop the task and flush what is left."""
        self._closed = True
        if self._wake is not None:
            self._wake.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover
                task.cancel()
        await self.flush()

    @property
    def pending(self) -> int:
        return len(self._items)

    # ── internals ────────────────────────────────────────────────────────────

    def _ensure_task(self) -> None:
        if self._closed or (self._task is not None and not self._task.done()):
            return
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                return
        except RuntimeError:  # pragma: no cover - no loop yet
            return
        self._wake = asyncio.Event()
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        assert self._wake is not None
        while not self._closed:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self._items:
                batch = self._items
                self._items = []
                await self._send_grouped(batch)

    async def _send_grouped(self, batch: List[Tuple[str, Dict[str, Any], Dict[str, Any]]]) -> None:
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
                    await self._sender(chunk, context)
                except Exception as exc:
                    logger.warning("nexus: %s delivery failed (%d items): %s", self.name, len(chunk), exc)
