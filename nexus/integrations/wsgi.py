"""Generic WSGI middleware — works with any WSGI framework.

Pyramid, Bottle, CherryPy, Falcon, web2py, a bare ``wsgiref`` app, or Django
and Flask if you prefer middleware over their native integrations::

    from nexus.integrations.wsgi import NexusWSGIMiddleware

    app = NexusWSGIMiddleware(app, api_key="nxs_live_xxx")

Already called :func:`nexus.init`? Leave the key out and it reuses that client.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from ._common import RequestInfo, RequestTracker, client_ip_from, parse_cookies, resolve_client

__all__ = ["NexusWSGIMiddleware"]


def _headers_from_environ(environ: Dict[str, Any]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            headers[key[5:].replace("_", "-").lower()] = str(value)
    for key, name in (("CONTENT_TYPE", "content-type"), ("CONTENT_LENGTH", "content-length")):
        if environ.get(key):
            headers[name] = str(environ[key])
    return headers


def request_info_from_environ(environ: Dict[str, Any]) -> RequestInfo:
    """Build a :class:`~nexus.integrations._common.RequestInfo` from a WSGI environ."""
    headers = _headers_from_environ(environ)
    path = environ.get("PATH_INFO") or "/"
    query = environ.get("QUERY_STRING") or None
    scheme = environ.get("wsgi.url_scheme", "http")
    host = headers.get("host") or environ.get("SERVER_NAME") or ""
    return RequestInfo(
        method=environ.get("REQUEST_METHOD", "GET"),
        path=path,
        url="{0}://{1}{2}".format(scheme, host, path) if host else path,
        headers=headers,
        cookies=parse_cookies(headers.get("cookie")),
        client_ip=client_ip_from(headers, environ.get("REMOTE_ADDR")),
        query=query,
    )


class NexusWSGIMiddleware:
    """Wraps a WSGI application with Nexus request telemetry."""

    def __init__(
        self,
        app: Callable[..., Iterable[bytes]],
        client: Any = None,
        *,
        api_key: Optional[str] = None,
        distinct_id_resolver: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
        **tracker_options: Any,
    ) -> None:
        """
        Args:
            app: The WSGI app to wrap.
            client: An existing :class:`nexus.NexusClient`.
            api_key: Build a client with this key when none is given.
            distinct_id_resolver: ``fn(environ) -> user id | None``.
            **tracker_options: Passed to
                :class:`~nexus.integrations._common.RequestTracker`
                (``track_requests``, ``track_sessions``, ``capture_errors``,
                ``event_name``, ``ignore_paths``, ``sample_rate``).
        """
        self.app = app
        self.client = resolve_client(client, api_key=api_key) if (client or api_key) else resolve_client()
        self.tracker = RequestTracker(
            self.client, distinct_id_resolver=distinct_id_resolver, **tracker_options
        )

    def __call__(self, environ: Dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        info = request_info_from_environ(environ)
        state = self.tracker.begin(info, environ)
        status_code: List[Optional[int]] = [None]

        def capture_status(status: str, headers: Any, exc_info: Any = None) -> Any:
            try:
                status_code[0] = int(str(status).split(" ", 1)[0])
            except (TypeError, ValueError):  # pragma: no cover - odd status line
                status_code[0] = None
            return start_response(status, headers, exc_info)

        try:
            result = self.app(environ, capture_status)
        except BaseException as exc:
            self.tracker.finish(state, status=500, exc=exc, request=environ)
            raise

        # The body may stream after the app returns, so finish only once the
        # iterable is exhausted (or closed).
        return _ClosingIterator(result, lambda: self.tracker.finish(
            state, status=status_code[0], request=environ
        ))


class _ClosingIterator:
    """Passes the response body through, running ``on_close`` at the end."""

    def __init__(self, iterable: Iterable[bytes], on_close: Callable[[], None]) -> None:
        self._iterable = iterable
        self._iterator = iter(iterable)
        self._on_close = on_close
        self._closed = False

    def __iter__(self) -> "_ClosingIterator":
        return self

    def __next__(self) -> bytes:
        try:
            return next(self._iterator)
        except StopIteration:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            inner_close = getattr(self._iterable, "close", None)
            if callable(inner_close):
                inner_close()
        finally:
            self._on_close()
