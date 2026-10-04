"""Product analytics — capture events correlated to a person and session."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .. import _payloads as p
from .base import Resource

__all__ = ["Events"]


class Events(Resource):
    """``client.events``

    ``track()`` is buffered by default (``NexusConfig.batch``) and shipped on a
    background thread, so it costs microseconds in a request handler. Call
    :meth:`flush` (or ``client.flush()``) to force delivery — the SDK also
    flushes at interpreter exit.
    """

    def track(
        self,
        name: str,
        properties: Optional[Mapping[str, Any]] = None,
        *,
        timestamp: Optional[str] = None,
        distinct_id: Optional[str] = None,
        session_key: Optional[str] = None,
        device_key: Optional[str] = None,
        release: Optional[str] = None,
        app_version: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        browser: Optional[str] = None,
        **extra_properties: Any,
    ) -> None:
        """Capture one event.

        ::

            client.events.track("order_placed", {"total": 42, "currency": "USD"})
            client.events.track("signup", plan="pro")       # kwargs become properties

        ``timestamp`` is ISO-8601; omit it and the server stamps arrival time.
        """
        merged: Dict[str, Any] = dict(self._client.config.default_properties)
        ambient = self._client.ambient_properties()
        if ambient:
            merged.update(ambient)
        if properties:
            merged.update(properties)
        if extra_properties:
            merged.update(extra_properties)

        item = p.event_item(name, merged or None, timestamp)
        context = self._context(
            distinct_id=distinct_id,
            session_key=session_key,
            device_key=device_key,
            release=release,
            app_version=app_version,
            os_type=os_type,
            os_version=os_version,
            browser=browser,
        )
        self._client.enqueue_event(item, context)

    #: ``capture`` is the spelling used by the PHP SDK; kept as an alias.
    capture = track

    def batch(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        distinct_id: Optional[str] = None,
        session_key: Optional[str] = None,
        device_key: Optional[str] = None,
        release: Optional[str] = None,
        app_version: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        browser: Optional[str] = None,
    ) -> int:
        """Send many events sharing one identity **immediately** (no buffering).

        Each item is ``{"name": str, "properties": dict, "timestamp": str}``.
        Returns the number of events the server wrote.
        """
        items: Sequence[Mapping[str, Any]] = [dict(e) for e in events]
        if not items:
            return 0
        context = self._context(
            distinct_id=distinct_id,
            session_key=session_key,
            device_key=device_key,
            release=release,
            app_version=app_version,
            os_type=os_type,
            os_version=os_version,
            browser=browser,
        )
        return self._written(self._send(p.events_batch(items, context)), len(items))

    def flush(self, timeout: Optional[float] = None) -> None:
        """Ship everything buffered right now."""
        self._client.flush_events(timeout)
