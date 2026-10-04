"""The asynchronous Nexus client — same surface, ``await``-ed.

::

    from nexus.aio import AsyncNexusClient

    nexus = AsyncNexusClient("nxs_live_xxx")

    await nexus.identify("user_123", email="a@b.com")
    nexus.events.track("order_placed", {"total": 42})      # buffered, no await
    await nexus.realtime.emit("orders:42", "status", {"state": "shipped"})
    await nexus.aclose()

Buffered telemetry (``events.track`` / ``logs.info``) stays synchronous — it
only appends to a buffer — while everything that talks to the network is a
coroutine. Build one client per process and close it on shutdown.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Union

from .. import _payloads as p
from .. import context as ctx
from .._engine import AsyncHttpEngine
from .._logging import logger, set_debug
from ..atransport import AsyncTransport
from ..config import NexusConfig
from ..exceptions import NexusError
from ._batcher import AsyncBatchQueue
from .resources import (
    AsyncErrors,
    AsyncEvents,
    AsyncFlags,
    AsyncInApp,
    AsyncLinks,
    AsyncLiveActivities,
    AsyncLogs,
    AsyncPush,
    AsyncRealtime,
    AsyncRemoteConfig,
    AsyncReplay,
    AsyncSessions,
    AsyncSurveys,
    AsyncVoice,
)

__all__ = ["AsyncNexusClient"]


class AsyncNexusClient:
    """An asyncio-native connection to one Nexus environment."""

    def __init__(
        self,
        api_key: Optional[Union[str, NexusConfig]] = None,
        *,
        config: Optional[NexusConfig] = None,
        transport: Optional[AsyncTransport] = None,
        **options: Any,
    ) -> None:
        if isinstance(api_key, NexusConfig):
            config = api_key
            api_key = None
        if config is None:
            config = NexusConfig.create(api_key, **options)
        elif options:
            config = config.with_(**options)

        self.config = config
        set_debug(config.debug)
        self.engine = AsyncHttpEngine(config, transport)
        self._closed = False
        self._pending_tasks: set = set()

        self._event_queue: Optional[AsyncBatchQueue] = None
        self._log_queue: Optional[AsyncBatchQueue] = None
        if config.batch:
            self._event_queue = AsyncBatchQueue(
                "events",
                self._deliver_events,
                max_batch=config.max_batch,
                interval=config.flush_interval,
                max_queue=config.max_queue,
            )
            self._log_queue = AsyncBatchQueue(
                "logs",
                self._deliver_logs,
                max_batch=config.max_batch,
                interval=config.flush_interval,
                max_queue=config.max_queue,
            )

        self.sessions = AsyncSessions(self)
        self.events = AsyncEvents(self)
        self.logs = AsyncLogs(self)
        self.errors = AsyncErrors(self)
        self.flags = AsyncFlags(self)
        self.remote_config = AsyncRemoteConfig(self)
        self.links = AsyncLinks(self)
        self.realtime = AsyncRealtime(self)
        self.surveys = AsyncSurveys(self)
        self.push = AsyncPush(self)
        self.inapp = AsyncInApp(self)
        self.live_activities = AsyncLiveActivities(self)
        self.replay = AsyncReplay(self)
        self.voice = AsyncVoice(self)

    # ── identity ─────────────────────────────────────────────────────────────

    async def identify(
        self,
        distinct_id: str,
        *,
        email: Optional[str] = None,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        traits: Optional[Mapping[str, Any]] = None,
        remote: bool = True,
        **context_fields: Any,
    ) -> Dict[str, Any]:
        """Bind the user to this task's context and upsert them server-side."""
        ctx.identify(distinct_id, **context_fields)
        if not remote:
            return {}
        return await self.sessions.identify(
            distinct_id, email=email, name=name, phone=phone, traits=traits
        )

    def reset(self) -> None:
        ctx.reset()

    @property
    def distinct_id(self) -> Optional[str]:
        return ctx.current().distinct_id

    @property
    def session_key(self) -> Optional[str]:
        return ctx.current().session_key

    def current_distinct_id(self) -> Optional[str]:
        return ctx.current().distinct_id

    def ambient_properties(self) -> Mapping[str, Any]:
        return ctx.current().properties

    def resolve_context(self, **overrides: Any) -> Dict[str, Any]:
        """Config defaults < ambient context < explicit overrides (camelCase)."""
        resolved: Dict[str, Any] = {}
        config = self.config
        if config.release:
            resolved["release"] = config.release
        if config.app_version:
            resolved["appVersion"] = config.app_version
        if config.os_type:
            resolved["osType"] = config.os_type
        if config.os_version:
            resolved["osVersion"] = config.os_version
        resolved.update(ctx.current().wire())
        resolved.update({k: v for k, v in overrides.items() if v is not None})
        return resolved

    # ── delivery ─────────────────────────────────────────────────────────────

    async def request(self, request: p.Request) -> Any:
        """Execute one partner-API request (raises unless ``silent``)."""
        if self._closed:
            logger.warning("nexus: request after aclose() — %s %s", request.method, request.path)
            return None
        try:
            return await self.engine.execute(request)
        except NexusError as exc:
            if self.config.silent:
                logger.warning("nexus: %s %s failed: %s", request.method, request.path, exc)
                return None
            raise

    def enqueue_event(self, item: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        if self._event_queue is not None:
            self._event_queue.add(item, context)
        else:
            self._spawn(self._deliver_events([dict(item)], dict(context)))

    def enqueue_log(self, item: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        if self._log_queue is not None:
            self._log_queue.add(item, context)
        else:
            self._spawn(self._deliver_logs([dict(item)], dict(context)))

    async def _deliver_events(self, items: Any, context: Any) -> None:
        await self.request(p.events_batch(items, context))

    async def _deliver_logs(self, items: Any, context: Any) -> None:
        await self.request(p.logs_batch(items, context))

    def _spawn(self, coro: Any) -> None:
        """Fire a coroutine without awaiting it (unbatched mode)."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = loop.create_task(coro)
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)
                return
        except RuntimeError:  # pragma: no cover - no loop
            pass
        coro.close()
        logger.warning("nexus: dropped telemetry — no running event loop")

    async def flush_events(self) -> None:
        if self._event_queue is not None:
            await self._event_queue.flush()

    async def flush_logs(self) -> None:
        if self._log_queue is not None:
            await self._log_queue.flush()

    async def flush(self) -> None:
        """Ship everything buffered right now."""
        await self.flush_events()
        await self.flush_logs()

    @property
    def pending(self) -> int:
        total = 0
        if self._event_queue is not None:
            total += self._event_queue.pending
        if self._log_queue is not None:
            total += self._log_queue.pending
        return total

    # ── shortcuts ────────────────────────────────────────────────────────────

    def track(self, name: str, properties: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> None:
        self.events.track(name, properties, **kwargs)

    async def capture_exception(self, exc: Optional[BaseException] = None, **options: Any) -> Dict[str, Any]:
        return await self.errors.capture_exception(exc, **options)

    async def emit(self, room: str, events: Any, payload: Any = None) -> Dict[str, Any]:
        return await self.realtime.emit(room, events, payload)

    async def is_enabled(self, key: str, distinct_id: Optional[str] = None, **kwargs: Any) -> bool:
        return await self.flags.is_enabled(key, distinct_id, **kwargs)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Flush, stop the background tasks and close the transport."""
        if self._closed:
            return
        try:
            if self._event_queue is not None:
                await self._event_queue.aclose()
            if self._log_queue is not None:
                await self._log_queue.aclose()
        finally:
            self._closed = True
            await self.engine.aclose()

    @property
    def closed(self) -> bool:
        return self._closed

    async def __aenter__(self) -> "AsyncNexusClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<AsyncNexusClient {0} key={1}…>".format(self.config.base_url, self.config.api_key[:12])
