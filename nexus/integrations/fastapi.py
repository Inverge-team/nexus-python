"""FastAPI / Starlette integration.

::

    from fastapi import FastAPI, Depends
    from nexus.integrations.fastapi import NexusMiddleware, get_nexus, lifespan_for

    app = FastAPI()
    app.add_middleware(NexusMiddleware, api_key="nxs_live_xxx")

    @app.get("/orders/{order_id}")
    async def show(order_id: int, nexus = Depends(get_nexus)):
        nexus.events.track("order_viewed", {"id": order_id})
        return {"ok": True}

The middleware binds the request's identity into :mod:`nexus.context`, so
anything captured during the request — including from a service layer that
never sees the request object — is correlated automatically.

Use ``lifespan_for(client)`` (or call ``client.flush()`` in your own shutdown
hook) so buffered telemetry is shipped before the process exits.

Works just as well with plain Starlette, Quart and Litestar; it is an ordinary
ASGI middleware underneath (:mod:`nexus.integrations.asgi`).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional

from .asgi import NexusASGIMiddleware, request_info_from_scope

__all__ = [
    "NexusMiddleware",
    "NexusASGIMiddleware",
    "get_nexus",
    "nexus_dependency",
    "lifespan_for",
    "request_info_from_scope",
]


class NexusMiddleware(NexusASGIMiddleware):
    """Starlette-style middleware (``app.add_middleware(NexusMiddleware, ...)``).

    Accepts the same options as :class:`nexus.integrations.asgi.NexusASGIMiddleware`:
    ``client``, ``api_key``, ``distinct_id_resolver``, ``track_requests``,
    ``track_sessions``, ``capture_errors``, ``event_name``, ``ignore_paths``,
    ``sample_rate``.
    """


def get_nexus() -> Any:
    """FastAPI dependency returning the default client.

    ::

        @app.get("/")
        async def index(nexus = Depends(get_nexus)): ...
    """
    from .._default import get_client

    return get_client()


def nexus_dependency(client: Any) -> Callable[[], Any]:
    """A dependency bound to a specific client (useful in tests)."""

    def dependency() -> Any:
        return client

    return dependency


def lifespan_for(client: Any = None, *, timeout: float = 5.0) -> Callable[[Any], Any]:
    """Build a FastAPI ``lifespan`` that flushes Nexus on shutdown.

    ::

        app = FastAPI(lifespan=lifespan_for())
    """

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        resolved = client
        if resolved is None:
            from .._default import get_client

            resolved = get_client(required=False)
        try:
            yield
        finally:
            if resolved is not None:
                closer = getattr(resolved, "aclose", None)
                if closer is not None:
                    await closer()
                else:
                    resolved.close(timeout)

    return lifespan


def default_distinct_id(scope: Any) -> Optional[str]:
    """Read ``scope["user"]`` when an auth middleware put one there."""
    user = scope.get("user") if isinstance(scope, dict) else None
    if user is None:
        return None
    for attr in ("id", "pk", "username", "identity"):
        value = getattr(user, attr, None)
        if value:
            return str(value)
    return None
