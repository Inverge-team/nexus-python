"""The Nexus client — one object, every Nexus product.

::

    from nexus import NexusClient

    nexus = NexusClient("nxs_live_xxx")
    nexus.identify("user_123", email="a@b.com")
    nexus.events.track("order_placed", {"total": 42})
    nexus.realtime.emit("orders:42", "status", {"state": "shipped"})

The client is thread-safe and meant to be built **once** per process and shared.
In a web app, build it at import time (or in your framework's startup hook) and
reuse it; the integrations in :mod:`nexus.integrations` do that for you.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Union

from . import _services
from . import _payloads as p
from . import context as ctx
from ._engine import HttpEngine
from ._logging import logger, set_debug
from .batcher import BatchQueue
from .config import NexusConfig
from .dispatcher import BackgroundDispatcher, DirectDispatcher, Dispatcher
from .exceptions import NexusError
from .resources import (
    Errors,
    Events,
    Flags,
    InApp,
    Links,
    LiveActivities,
    Logs,
    Push,
    Realtime,
    RemoteConfig,
    Replay,
    Sessions,
    Shield,
    Surveys,
    Voice,
)
from .transport import Transport

__all__ = ["NexusClient"]


class NexusClient:
    """A configured connection to one Nexus environment."""

    def __init__(
        self,
        api_key: Optional[Union[str, NexusConfig]] = None,
        *,
        config: Optional[NexusConfig] = None,
        transport: Optional[Transport] = None,
        dispatcher: Optional[Dispatcher] = None,
        **options: Any,
    ) -> None:
        """
        Args:
            api_key: The ``nxs_...`` key, or a ready :class:`NexusConfig`.
            config: A :class:`NexusConfig` (alternative to ``api_key``).
            transport: Custom HTTP transport. Defaults to the best installed.
            dispatcher: Custom delivery strategy (see :mod:`nexus.dispatcher`).
            **options: Any :class:`NexusConfig` field, e.g. ``base_url=...``,
                ``batch=False``, ``app_version="1.4.2"``.
        """
        if isinstance(api_key, NexusConfig):
            config = api_key
            api_key = None
        if config is None:
            # Explicit options layered over NEXUS_* — so NEXUS_BASE_URL can
            # repoint a deploy even when the key is passed in code.
            config = NexusConfig.create(api_key, **options)
        elif options:
            config = config.with_(**options)

        self.config = config
        set_debug(config.debug)

        self.engine = HttpEngine(config, transport)
        self.dispatcher: Dispatcher = dispatcher or DirectDispatcher(self.engine)
        self._closed = False
        self._skipped: set = set()  # switched-off services already logged

        # Buffered ingest for the two high-volume products.
        self._event_queue: Optional[BatchQueue] = None
        self._log_queue: Optional[BatchQueue] = None
        if config.batch:
            self._event_queue = BatchQueue(
                "events",
                self._deliver_events,
                max_batch=config.max_batch,
                interval=config.flush_interval,
                max_queue=config.max_queue,
            )
            self._log_queue = BatchQueue(
                "logs",
                self._deliver_logs,
                max_batch=config.max_batch,
                interval=config.flush_interval,
                max_queue=config.max_queue,
            )

        # Resources
        self.sessions = Sessions(self)
        self.events = Events(self)
        self.logs = Logs(self)
        self.errors = Errors(self)
        self.flags = Flags(self)
        self.remote_config = RemoteConfig(self)
        self.links = Links(self)
        self.realtime = Realtime(self)
        self.surveys = Surveys(self)
        self.push = Push(self)
        self.inapp = InApp(self)
        self.live_activities = LiveActivities(self)
        self.replay = Replay(self)
        self.voice = Voice(self)
        self.shield = Shield(self)

    # ── identity ─────────────────────────────────────────────────────────────

    def identify(
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
        """Set the current user locally **and** upsert them server-side.

        Pass ``remote=False`` to only bind the local context (useful in a hot
        loop where the person already exists).
        """
        ctx.identify(distinct_id, **context_fields)
        if not remote:
            return {}
        return self.sessions.identify(
            distinct_id, email=email, name=name, phone=phone, traits=traits
        )

    def reset(self) -> None:
        """Forget the user and start a new session — call this on logout."""
        ctx.reset()

    @property
    def distinct_id(self) -> Optional[str]:
        """The user bound to the current context, if any."""
        return ctx.current().distinct_id

    @property
    def session_key(self) -> Optional[str]:
        """The session bound to the current context, if any."""
        return ctx.current().session_key

    def current_distinct_id(self) -> Optional[str]:
        return ctx.current().distinct_id

    def ambient_properties(self) -> Mapping[str, Any]:
        """Properties bound via :func:`nexus.context.scope`."""
        return ctx.current().properties

    def resolve_context(self, **overrides: Any) -> Dict[str, Any]:
        """Merge config defaults, ambient context and explicit overrides.

        Keys are the API's camelCase field names. Precedence, lowest first:
        client config → :mod:`nexus.context` → ``overrides``.
        """
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

    def request(self, request: p.Request) -> Any:
        """Execute one partner-API request.

        Raises a :class:`~nexus.exceptions.NexusError` subclass on failure,
        unless the client is configured ``silent=True`` — then it logs and
        returns ``None``.
        """
        if self._switched_off(request.path):
            return None
        if self._closed:
            logger.warning("nexus: request after close() — %s %s", request.method, request.path)
            return None
        try:
            return self.dispatcher.dispatch(request)
        except NexusError as exc:
            if self.config.silent:
                logger.warning("nexus: %s %s failed: %s", request.method, request.path, exc)
                return None
            raise

    def enabled(self, service: str) -> bool:
        """Whether a product is switched on — ``services`` / ``NEXUS_<SERVICE>_ENABLED``."""
        return self.config.service_enabled(service)

    def _switched_off(self, path: str) -> bool:
        """A switched-off service is a no-op: no network, no exception, neutral result."""
        service = _services.service_for_path(path)
        if service is None or self.config.service_enabled(service):
            return False
        if service not in self._skipped:
            self._skipped.add(service)
            logger.info("nexus: %s is switched off (%s) — skipping its calls", service, self.config.services_off[service])
        return True

    def enqueue_event(self, item: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        """Buffer one event (or send it now when batching is off)."""
        if not self.config.service_enabled("events"):
            return  # switched off — don't even buffer
        if self._event_queue is not None:
            self._event_queue.add(item, context)
        else:
            self._deliver_events([dict(item)], dict(context))

    def enqueue_log(self, item: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        """Buffer one log line (or send it now when batching is off)."""
        if not self.config.service_enabled("logs"):
            return  # switched off — don't even buffer
        if self._log_queue is not None:
            self._log_queue.add(item, context)
        else:
            self._deliver_logs([dict(item)], dict(context))

    def _deliver_events(self, items: Any, context: Any) -> None:
        self.request(p.events_batch(items, context))

    def _deliver_logs(self, items: Any, context: Any) -> None:
        self.request(p.logs_batch(items, context))

    def flush_events(self, timeout: Optional[float] = None) -> None:
        if self._event_queue is not None:
            self._event_queue.flush(timeout)

    def flush_logs(self, timeout: Optional[float] = None) -> None:
        if self._log_queue is not None:
            self._log_queue.flush(timeout)

    def flush(self, timeout: Optional[float] = None) -> None:
        """Ship every buffered event and log line, and drain any background
        dispatcher. Call it before a short-lived process exits (the SDK also
        does this at interpreter exit)."""
        self.flush_events(timeout)
        self.flush_logs(timeout)
        self.dispatcher.flush(timeout)

    @property
    def pending(self) -> int:
        """How many buffered items are waiting to be sent."""
        total = 0
        if self._event_queue is not None:
            total += self._event_queue.pending
        if self._log_queue is not None:
            total += self._log_queue.pending
        return total

    # ── variants ─────────────────────────────────────────────────────────────

    def with_dispatcher(self, dispatcher: Dispatcher) -> "NexusClient":
        """A sibling client that delivers through a different dispatcher.

        The two share nothing but configuration, so buffering and shutdown stay
        independent.
        """
        return NexusClient(config=self.config, dispatcher=dispatcher)

    def background(self, *, workers: int = 1) -> "NexusClient":
        """A sibling client where **every** call is fire-and-forget.

        ::

            bg = nexus.background()
            bg.push.send_to_user("u_1", title="Welcome")   # returns None

        Result-returning calls (flags, Remote Config) can't work here — use the
        normal client for those.
        """
        return self.with_dispatcher(BackgroundDispatcher(self.engine, workers=workers))

    # ── shortcuts ────────────────────────────────────────────────────────────

    def track(self, name: str, properties: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> None:
        """Shortcut for ``client.events.track``."""
        self.events.track(name, properties, **kwargs)

    def capture_exception(self, exc: Optional[BaseException] = None, **options: Any) -> Dict[str, Any]:
        """Shortcut for ``client.errors.capture_exception``."""
        return self.errors.capture_exception(exc, **options)

    def emit(self, room: str, events: Any, payload: Any = None) -> Dict[str, Any]:
        """Shortcut for ``client.realtime.emit``."""
        return self.realtime.emit(room, events, payload)

    def is_enabled(self, key: str, distinct_id: Optional[str] = None, **kwargs: Any) -> bool:
        """Shortcut for ``client.flags.is_enabled``."""
        return self.flags.is_enabled(key, distinct_id, **kwargs)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def close(self, timeout: float = 3.0) -> None:
        """Flush and release everything. Idempotent."""
        if self._closed:
            return
        try:
            if self._event_queue is not None:
                self._event_queue.close(timeout)
            if self._log_queue is not None:
                self._log_queue.close(timeout)
            self.dispatcher.close(timeout)
        finally:
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "NexusClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "<NexusClient {0} key={1}…>".format(self.config.base_url, self.config.api_key[:12])
