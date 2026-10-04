"""Generic ASGI middleware — FastAPI, Starlette, Quart, Litestar, Falcon ASGI…

::

    from nexus.integrations.asgi import NexusASGIMiddleware

    app = NexusASGIMiddleware(app, api_key="nxs_live_xxx")

FastAPI and Starlette users can also add it the Starlette way — see
:mod:`nexus.integrations.fastapi`.

Both client flavours work: pass a :class:`nexus.NexusClient` (telemetry is
buffered and shipped off-thread, so it does not block the loop) or a
:class:`nexus.aio.AsyncNexusClient` to stay fully async.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Optional

from ._common import RequestInfo, RequestTracker, client_ip_from, parse_cookies, resolve_client

__all__ = ["NexusASGIMiddleware", "request_info_from_scope"]


def request_info_from_scope(scope: Dict[str, Any]) -> RequestInfo:
    """Build a :class:`~nexus.integrations._common.RequestInfo` from an ASGI scope."""
    from ._common import iter_header_pairs

    headers = iter_header_pairs(scope.get("headers") or [])
    path = scope.get("path") or "/"
    raw_query = scope.get("query_string") or b""
    query = raw_query.decode("latin-1") if isinstance(raw_query, bytes) else str(raw_query)
    scheme = scope.get("scheme") or "http"
    host = headers.get("host") or ""
    client = scope.get("client")
    peer = client[0] if isinstance(client, (list, tuple)) and client else None

    route = None
    route_obj = scope.get("route")
    if route_obj is not None:
        route = getattr(route_obj, "path", None)

    return RequestInfo(
        method=scope.get("method", "GET"),
        path=path,
        route=route,
        url="{0}://{1}{2}".format(scheme, host, path) if host else path,
        headers=headers,
        cookies=parse_cookies(headers.get("cookie")),
        client_ip=client_ip_from(headers, peer),
        query=query or None,
    )


class NexusASGIMiddleware:
    """Wraps an ASGI application with Nexus request telemetry."""

    def __init__(
        self,
        app: Any,
        client: Any = None,
        *,
        api_key: Optional[str] = None,
        distinct_id_resolver: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
        **tracker_options: Any,
    ) -> None:
        self.app = app
        self.client = resolve_client(client, api_key=api_key) if (client or api_key) else resolve_client()
        self.tracker = RequestTracker(
            self.client, distinct_id_resolver=distinct_id_resolver, **tracker_options
        )
        # An AsyncNexusClient's capture/track differ (coroutines) — detect once.
        self._async_client = inspect.iscoroutinefunction(
            getattr(getattr(self.client, "errors", None), "capture_exception", None)
        )

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        info = request_info_from_scope(scope)
        state = self.tracker.begin(info, scope) if not self._async_client else self._begin_async(info, scope)
        status: Dict[str, Any] = {"code": None}

        async def send_wrapper(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = message.get("status")
                # The router resolves the route after begin(); pick it up now so
                # the event records "/orders/{id}", not "/orders/42".
                route_obj = scope.get("route")
                if route_obj is not None and getattr(route_obj, "path", None):
                    info.route = route_obj.path
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException as exc:
            await self._finish(state, info, 500, exc, scope)
            raise
        await self._finish(state, info, status["code"], None, scope)

    # ── internals ────────────────────────────────────────────────────────────

    def _begin_async(self, info: RequestInfo, scope: Dict[str, Any]) -> Dict[str, Any]:
        # Context binding and event buffering are synchronous even on the async
        # client; only the optional session refresh would await, so skip it.
        return self.tracker.begin(info, scope, track_sessions=False)

    async def _finish(
        self,
        state: Dict[str, Any],
        info: RequestInfo,
        status: Optional[int],
        exc: Optional[BaseException],
        scope: Dict[str, Any],
    ) -> None:
        if not self._async_client:
            self.tracker.finish(state, status=status, exc=exc, request=scope)
            return

        from .. import context as ctx
        from .._logging import logger

        try:
            if exc is not None and self.tracker.capture_errors:
                await self.client.errors.capture_exception(
                    exc,
                    handled=False,
                    level="error",
                    url=info.url or info.path,
                    context={"method": info.method, "path": info.route or info.path, "status": status},
                )
            if self.tracker.track_requests and not self.tracker.ignored(info.path):
                import time

                properties: Dict[str, Any] = {"method": info.method, "path": info.route or info.path}
                if status is not None:
                    properties["status"] = status
                started = state.get("started")
                if started:
                    properties["duration_ms"] = int((time.monotonic() - started) * 1000)
                self.client.events.track(self.tracker.event_name, properties)
        except Exception as error:  # telemetry must never break a response
            logger.warning("nexus: request telemetry failed: %s", error)
        finally:
            token = state.get("token")
            if token is not None:
                ctx.unbind(token)
