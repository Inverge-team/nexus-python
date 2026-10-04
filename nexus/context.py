"""Ambient telemetry context — who and what the current work belongs to.

Every event, log line and error carries identity (``distinct_id``), a session
(``session_key``) and device/app fields. Threading those through every call by
hand is miserable, so the SDK keeps them in a :mod:`contextvars` variable that
is correct per-thread **and** per-async-task: bind once at the edge of a
request, and everything captured underneath inherits it.

::

    from nexus import context

    with context.scope(distinct_id="user_123", url="/checkout"):
        nexus.events.track("order_placed", {"total": 42})   # carries user_123

The framework integrations bind this for you on every request.
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Iterator, Mapping, Optional

__all__ = [
    "TelemetryContext",
    "current",
    "bind",
    "unbind",
    "scope",
    "identify",
    "reset",
    "clear",
    "new_session_key",
    "new_device_key",
]

_FIELDS = (
    "distinct_id",
    "session_key",
    "device_key",
    "release",
    "app_version",
    "os_type",
    "os_version",
    "browser",
    "country",
    "url",
)

# camelCase wire names for the snake_case fields above.
_WIRE = {
    "distinct_id": "distinctId",
    "session_key": "sessionKey",
    "device_key": "deviceKey",
    "release": "release",
    "app_version": "appVersion",
    "os_type": "osType",
    "os_version": "osVersion",
    "browser": "browser",
    "country": "country",
    "url": "url",
}


@dataclass(frozen=True)
class TelemetryContext:
    """Identity + device fields attached to telemetry captured in this scope."""

    distinct_id: Optional[str] = None
    session_key: Optional[str] = None
    device_key: Optional[str] = None
    release: Optional[str] = None
    app_version: Optional[str] = None
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    browser: Optional[str] = None
    country: Optional[str] = None
    url: Optional[str] = None
    properties: Mapping[str, Any] = field(default_factory=dict)

    def merged(self, **changes: Any) -> "TelemetryContext":
        """A copy with the non-``None`` ``changes`` applied."""
        properties = changes.pop("properties", None)
        clean = {k: v for k, v in changes.items() if v is not None and k in _FIELDS}
        merged_props = dict(self.properties)
        if properties:
            merged_props.update(properties)
        return replace(self, properties=merged_props, **clean)

    def as_dict(self) -> Dict[str, Any]:
        """Snake_case dict of the set fields (``properties`` included)."""
        return {k: v for k, v in asdict(self).items() if v}

    def wire(self) -> Dict[str, Any]:
        """camelCase dict of the set identity fields, ready for the API."""
        out: Dict[str, Any] = {}
        for name in _FIELDS:
            value = getattr(self, name)
            if value is not None:
                out[_WIRE[name]] = value
        return out


_EMPTY = TelemetryContext()
_var: "contextvars.ContextVar[TelemetryContext]" = contextvars.ContextVar(
    "nexus_context", default=_EMPTY
)


def current() -> TelemetryContext:
    """The context in force for this thread / async task."""
    return _var.get()


def bind(**values: Any) -> "contextvars.Token[TelemetryContext]":
    """Merge ``values`` into the current context.

    Returns a token; pass it to :func:`unbind` to restore the previous state.
    Prefer :func:`scope`, which does that for you.
    """
    return _var.set(current().merged(**values))


def unbind(token: "contextvars.Token[TelemetryContext]") -> None:
    """Restore the context captured by :func:`bind`."""
    try:
        _var.reset(token)
    except ValueError:  # pragma: no cover - token from another context
        _var.set(_EMPTY)


@contextmanager
def scope(**values: Any) -> Iterator[TelemetryContext]:
    """Bind ``values`` for the duration of the ``with`` block.

    ::

        with context.scope(distinct_id="u_1", url=request.path):
            ...
    """
    token = bind(**values)
    try:
        yield current()
    finally:
        unbind(token)


def identify(distinct_id: Optional[str], **values: Any) -> None:
    """Set the current user (and optionally more fields) for this context.

    This only sets local context. To also upsert the person server-side, call
    ``client.sessions.identify(...)`` (or ``client.identify(...)``, which does
    both).
    """
    _var.set(current().merged(distinct_id=distinct_id, **values))


def clear() -> None:
    """Drop the whole context — no user, no session, no device.

    :func:`reset` is what you want on logout (it keeps the device and starts a
    fresh session); this is for tests and worker teardown.
    """
    _var.set(TelemetryContext())


def reset(*, keep_device: bool = True) -> None:
    """Forget the user and start a new session — call this on logout."""
    previous = current()
    _var.set(
        TelemetryContext(
            device_key=previous.device_key if keep_device else None,
            session_key=new_session_key(),
            release=previous.release,
            app_version=previous.app_version,
            os_type=previous.os_type,
            os_version=previous.os_version,
        )
    )


def new_session_key() -> str:
    """A fresh opaque session key."""
    return "sess_" + uuid.uuid4().hex


def new_device_key() -> str:
    """A fresh opaque device key."""
    return "dev_" + uuid.uuid4().hex
