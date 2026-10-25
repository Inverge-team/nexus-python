"""Nexus Telescope — request journeys for Python.

::

    import nexus.telescope as telescope

    telescope.init(api_key="nxs_live_xxx", service="orders-api")

Then add the integration for your framework:

- Django: ``"nexus.telescope.integrations.DjangoTelescopeMiddleware"`` first in ``MIDDLEWARE``
- FastAPI / Starlette: ``app.add_middleware(TelescopeASGIMiddleware)``
- Flask: ``instrument_flask(app)``
- Any WSGI / ASGI app: :class:`TelescopeWSGIMiddleware` / :class:`TelescopeASGIMiddleware`
- Celery: ``instrument_celery()``

``init()`` also instruments ``requests``, ``httpx`` (sync + async, with a W3C
``traceparent`` so downstream services join the trace), SQLAlchemy, redis-py
and the ``logging`` module. Everything is best-effort and never raises into
your app. Exceptions and log lines are also forwarded by Nexus to Errors and
Logs.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Dict, Optional, TypeVar

from ._recorder import Batch, Recorder, new_span_id
from ._redact import safe
from .instrument import (
    TelescopeLogHandler,
    instrument_httpx,
    instrument_logging,
    instrument_redis,
    instrument_requests,
    instrument_sqlalchemy,
)
from .integrations import (
    DjangoTelescopeMiddleware,
    TelescopeASGIMiddleware,
    TelescopeWSGIMiddleware,
    instrument_celery,
    instrument_flask,
)

__all__ = [
    "Telescope",
    "init",
    "get",
    "TelescopeWSGIMiddleware",
    "TelescopeASGIMiddleware",
    "DjangoTelescopeMiddleware",
    "TelescopeLogHandler",
    "instrument_flask",
    "instrument_celery",
]

T = TypeVar("T")

_instance: Optional["Telescope"] = None
_lock = threading.Lock()


class Telescope:
    """The public Telescope API (one per process — see :func:`init`)."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    # units of work outside HTTP
    def job(self, name: str, **kw: Any):
        """``with telescope.job("send_invoice", carrier=payload):`` — joins the trace in ``carrier``."""
        carrier = kw.pop("carrier", None) or {}
        return self.recorder.unit("job", name, kw or None, traceparent=carrier.get("__nexusTelescope") or carrier.get("traceparent"))

    def command(self, name: str, **content: Any):
        return self.recorder.unit("command", name, {"command": name, **content})

    def schedule(self, name: str, expression: Optional[str] = None):
        return self.recorder.unit("schedule", name, {"description": name, "expression": expression})

    def inject(self, job_name: Optional[str] = None, queue: Optional[str] = None) -> Dict[str, str]:
        """Fields to merge into a job payload so its run joins this trace; records the dispatch."""
        span = new_span_id()
        tp = self.recorder.traceparent(span)
        if tp is None:
            return {}
        if job_name:
            self.recorder.record("job", {"name": job_name, "queue": queue, "status": "dispatched"}, id=span, status="dispatched")
        return {"__nexusTelescope": tp}

    # entries
    def span(self, name: str, **content: Any):
        """``with telescope.span("pricing"):`` — entries inside nest under it."""
        return self.recorder.span(name, content or None)

    def traced(self, name: Optional[str] = None) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator form of :meth:`span` (sync and async functions)."""
        import functools
        import inspect

        def deco(fn: Callable[..., T]) -> Callable[..., T]:
            label = name or fn.__qualname__
            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def awrapper(*a: Any, **k: Any) -> Any:
                    with self.recorder.span(label):
                        return await fn(*a, **k)

                return awrapper  # type: ignore[return-value]

            @functools.wraps(fn)
            def wrapper(*a: Any, **k: Any) -> Any:
                with self.recorder.span(label):
                    return fn(*a, **k)

            return wrapper

        return deco

    def record(self, kind: str, content: Dict[str, Any], **opts: Any) -> Optional[str]:
        return self.recorder.record(kind, content, **opts)

    def query(self, sql: str, bindings: Any = None, duration_ms: Optional[float] = None, connection: Optional[str] = None) -> None:
        self.recorder.record("query", {"sql": sql, "bindings": safe(bindings or []), "connection": connection}, duration_ms=duration_ms)

    def event(self, name: str, payload: Any = None) -> None:
        self.recorder.record("event", {"name": name, "payload": safe(payload)})

    def cache(self, action: str, key: str, value: Any = None) -> None:
        self.recorder.record("cache", {"action": action, "key": key, "value": safe(value)}, status=action)

    def log(self, level: str, message: str, **context: Any) -> None:
        self.recorder.log(level, message, context or None)

    def exception(self, exc: BaseException, handled: bool = True, **context: Any) -> None:
        self.recorder.exception(exc, handled, context or None)

    def user(self, id: Any, **fields: Any) -> None:
        """The signed-in user: tags the journey ``Auth:<id>`` and links it to Nexus Sessions."""
        self.recorder.user(id=id, **fields)

    def tag(self, *tags: str) -> None:
        self.recorder.tag(*tags)

    def traceparent(self) -> Optional[str]:
        return self.recorder.traceparent()

    def flush(self, timeout: Optional[float] = None) -> None:
        self.recorder.flush(timeout)


def init(
    client: Any = None,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    instrument: bool = True,
    **options: Any,
) -> Telescope:
    """Create (once) the process-wide Telescope. Options: ``service``, ``release``,
    ``sample_rate``, ``ignore_paths``, ``ignore_types``, ``redact_keys``,
    ``capture_bodies``, ``body_size_limit``, ``max_entries``, ``enabled``."""
    global _instance
    with _lock:
        if _instance is not None:
            return _instance
        if client is None:
            from ..integrations._common import resolve_client

            init_options = {k: v for k, v in {"api_key": api_key or os.environ.get("NEXUS_API_KEY"), "base_url": base_url}.items() if v}
            client = resolve_client(None, **init_options)
        _instance = Telescope(Recorder(client, **options))
        if instrument:
            rec = _instance.recorder
            for fn in (instrument_requests, instrument_httpx, instrument_sqlalchemy, instrument_redis, instrument_logging):
                try:
                    fn(rec)
                except Exception:  # pragma: no cover - a library we could not patch is skipped
                    pass
        return _instance


def get() -> Optional[Telescope]:
    """The Telescope created by :func:`init`, if any."""
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
