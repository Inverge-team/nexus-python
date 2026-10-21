"""Receive realtime events in Python (optional).

The HTTP ``client.realtime`` resource only *emits*. To **listen** — a worker
that reacts to room traffic, a bridge, a test harness — connect a Socket.IO
client::

    pip install "inverge-nexus[realtime]"

::

    socket = nexus.RealtimeSocket(client)
    socket.connect()
    socket.join("orders:42")

    @socket.on("status")
    def on_status(data):
        print("order status:", data)

    socket.wait()          # block the thread, or just let your app run

Rooms are **environment-scoped**, so the key you connect with must belong to
the same environment that emits — a mismatch looks exactly like silence.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Set, Union

from . import context as ctx
from ._logging import logger
from .config import NexusConfig
from .exceptions import ConfigurationError

__all__ = ["RealtimeSocket", "EventHandler"]

EventHandler = Callable[[Any], Any]


class RealtimeSocket:
    """A Socket.IO connection to the Nexus realtime plane.

    Requires ``python-socketio[client]``. Handlers registered before connecting
    are attached on connect; joined rooms are re-joined after every reconnect
    (server-side membership does not survive one).
    """

    def __init__(
        self,
        client_or_config: Union[Any, NexusConfig, None] = None,
        *,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        distinct_id: Optional[str] = None,
        reconnection: bool = True,
        **socketio_options: Any,
    ) -> None:
        config = self._resolve_config(client_or_config, api_key)
        self.config = config
        self.url = url or config.socket_base
        self.distinct_id = distinct_id
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._rooms: Set[str] = set()
        self._lock = threading.RLock()
        self._sio: Any = None
        self._socketio_options = socketio_options
        self._reconnection = reconnection

    @staticmethod
    def _resolve_config(source: Any, api_key: Optional[str]) -> NexusConfig:
        if isinstance(source, NexusConfig):
            return source
        if source is not None and hasattr(source, "config"):
            return source.config
        if api_key:
            return NexusConfig(api_key=api_key)
        from ._default import get_client

        client = get_client(required=False)
        if client is not None:
            return client.config
        return NexusConfig.from_env()

    # ── connection ───────────────────────────────────────────────────────────

    def _require_socketio(self) -> Any:
        try:
            import socketio
        except ImportError as exc:  # pragma: no cover - import guard
            raise ConfigurationError(
                "RealtimeSocket needs python-socketio: pip install 'inverge-nexus[realtime]'"
            ) from exc
        return socketio

    def _auth(self) -> Dict[str, Any]:
        current = ctx.current()
        auth: Dict[str, Any] = {"key": self.config.api_key}
        distinct_id = self.distinct_id or current.distinct_id
        if distinct_id:
            auth["distinctId"] = distinct_id
        if current.session_key:
            auth["sessionKey"] = current.session_key
        if current.device_key:
            auth["deviceKey"] = current.device_key
        if current.os_type or self.config.os_type:
            auth["os"] = current.os_type or self.config.os_type
        if current.os_version or self.config.os_version:
            auth["osVersion"] = current.os_version or self.config.os_version
        return auth

    def connect(self, *, wait: bool = True, timeout: float = 10.0) -> "RealtimeSocket":
        """Open the connection. Idempotent. A no-op when realtime is switched off."""
        if not self.config.service_enabled("realtime"):
            logger.info("nexus: realtime is switched off (%s) — not connecting", self.config.services_off["realtime"])
            return self
        with self._lock:
            if self._sio is not None and getattr(self._sio, "connected", False):
                return self
            socketio = self._require_socketio()
            sio = socketio.Client(reconnection=self._reconnection, **self._socketio_options)

            for event, handlers in self._handlers.items():
                for handler in handlers:
                    sio.on(event, handler)

            @sio.event
            def connect() -> None:  # noqa: D401 - socket.io callback
                # Room membership is lost on reconnect; re-join everything.
                for room in list(self._rooms):
                    sio.emit("room.join", {"name": room})
                logger.info("nexus: realtime connected (%s)", self.url)

            @sio.event
            def disconnect() -> None:  # noqa: D401 - socket.io callback
                logger.info("nexus: realtime disconnected")

            self._sio = sio

        self._sio.connect(
            self.url,
            auth=self._auth(),
            headers={"x-api-key": self.config.api_key},
            transports=["websocket", "polling"],
            wait=wait,
            wait_timeout=timeout,
        )
        return self

    @property
    def connected(self) -> bool:
        return bool(self._sio is not None and getattr(self._sio, "connected", False))

    def disconnect(self) -> None:
        """Close the connection."""
        sio = self._sio
        self._sio = None
        if sio is not None:
            try:
                sio.disconnect()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("nexus: realtime disconnect failed: %s", exc)

    def wait(self) -> None:
        """Block the calling thread until the connection drops."""
        if self._sio is not None:
            self._sio.wait()

    # ── rooms ────────────────────────────────────────────────────────────────

    def join(self, room: str) -> Any:
        """Join a room (idempotent; re-joined automatically after reconnects)."""
        with self._lock:
            already = room in self._rooms
            self._rooms.add(room)
        if already and self.connected:
            return {"ok": True, "room": room, "already": True}
        return self._ack_emit("room.join", {"name": room})

    def leave(self, room: str) -> Any:
        with self._lock:
            self._rooms.discard(room)
        return self._ack_emit("room.leave", {"name": room})

    def emit(self, room: str, event: Union[str, List[str]], payload: Any = None) -> Any:
        """Emit over the socket (the HTTP ``client.realtime.emit`` is usually
        the better choice from a server)."""
        return self._ack_emit("room.emit", {"name": room, "event": event, "payload": payload})

    # ── handlers ─────────────────────────────────────────────────────────────

    def on(self, event: str, handler: Optional[EventHandler] = None) -> Any:
        """Register a handler. Usable directly or as a decorator::

            @socket.on("status")
            def handle(data): ...
        """

        def register(fn: EventHandler) -> EventHandler:
            with self._lock:
                handlers = self._handlers.setdefault(event, [])
                if fn not in handlers:
                    handlers.append(fn)
                    if self._sio is not None:
                        self._sio.on(event, fn)
            return fn

        if handler is None:
            return register
        return register(handler)

    def off(self, event: str, handler: Optional[EventHandler] = None) -> None:
        """Remove one handler, or every handler for the event."""
        with self._lock:
            if handler is None:
                self._handlers.pop(event, None)
            else:
                handlers = self._handlers.get(event, [])
                if handler in handlers:
                    handlers.remove(handler)
                if not handlers:
                    self._handlers.pop(event, None)

    # ── internals ────────────────────────────────────────────────────────────

    def _ack_emit(self, message: str, body: Dict[str, Any], timeout: float = 10.0) -> Any:
        if not self.connected:
            self.connect()
        if self._sio is None:  # pragma: no cover - connect failed
            return None
        try:
            return self._sio.call(message, body, timeout=timeout)
        except Exception as exc:
            logger.warning("nexus: realtime %s failed: %s", message, exc)
            return None

    def __enter__(self) -> "RealtimeSocket":
        return self.connect()

    def __exit__(self, *exc_info: Any) -> None:
        self.disconnect()
