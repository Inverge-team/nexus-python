"""Shared per-request telemetry logic behind every framework integration.

Keeping it here means Django, Flask, FastAPI and the generic WSGI/ASGI
middlewares behave identically — same context binding, same event shape, same
error reporting — and a fix lands everywhere at once.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .. import context as ctx
from .._logging import logger

__all__ = ["RequestInfo", "RequestTracker", "DEFAULT_IGNORE_PATHS"]

#: Paths not worth a telemetry event by default.
DEFAULT_IGNORE_PATHS: Tuple[str, ...] = (
    "/health",
    "/healthz",
    "/readyz",
    "/livez",
    "/ping",
    "/metrics",
    "/favicon.ico",
    "/static/",
    "/_next/",
)

#: Headers a client SDK uses to pass its journey identity to your backend, so a
#: browser session and the server work it triggers land on the same timeline.
SESSION_HEADER = "x-nexus-session-key"
DEVICE_HEADER = "x-nexus-device-key"
DISTINCT_HEADER = "x-nexus-distinct-id"
SESSION_COOKIE = "nexus_sid"
DEVICE_COOKIE = "nexus_did"


@dataclass
class RequestInfo:
    """A framework-neutral view of one inbound request."""

    method: str = "GET"
    path: str = "/"
    url: Optional[str] = None
    route: Optional[str] = None
    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)
    client_ip: Optional[str] = None
    query: Optional[str] = None

    def header(self, name: str) -> Optional[str]:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None

    @property
    def user_agent(self) -> Optional[str]:
        return self.header("user-agent")


class RequestTracker:
    """Per-request context binding, session refresh, event and error capture.

    Every framework integration owns one of these and calls
    :meth:`begin` / :meth:`finish` around the request.
    """

    def __init__(
        self,
        client: Any,
        *,
        track_requests: bool = True,
        track_sessions: bool = False,
        capture_errors: bool = True,
        event_name: str = "http_request",
        distinct_id_resolver: Optional[Callable[[Any], Optional[str]]] = None,
        ignore_paths: Sequence[str] = DEFAULT_IGNORE_PATHS,
        sample_rate: float = 1.0,
        record_query: bool = False,
    ) -> None:
        """
        Args:
            client: The :class:`nexus.NexusClient` to report through.
            track_requests: Capture an event per request.
            track_sessions: Also call ``sessions.track`` — one extra HTTP call
                per request, so it is **off** by default. Turn it on when the
                backend is the only thing talking to Nexus (no browser SDK).
            capture_errors: Report unhandled exceptions.
            event_name: Name of the per-request event.
            distinct_id_resolver: ``fn(framework_request) -> user id | None``.
            ignore_paths: Path prefixes that produce no telemetry.
            sample_rate: Fraction of requests that produce an event (0–1).
                Errors are always reported, whatever the sample rate.
            record_query: Include the query string in the event properties.
                Off by default — query strings leak tokens and PII.
        """
        self.client = client
        self.track_requests = track_requests
        self.track_sessions = track_sessions
        self.capture_errors = capture_errors
        self.event_name = event_name
        self.distinct_id_resolver = distinct_id_resolver
        self.ignore_paths = tuple(ignore_paths or ())
        self.sample_rate = max(0.0, min(1.0, float(sample_rate)))
        self.record_query = record_query

    # ── lifecycle ────────────────────────────────────────────────────────────

    def ignored(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.ignore_paths)

    def begin(
        self, info: RequestInfo, request: Any = None, *, track_sessions: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Bind request context. Returns a state dict to hand to :meth:`finish`.

        ``track_sessions`` overrides the instance setting for this request —
        the async middleware uses it to skip the (awaitable) session call.
        """
        distinct_id = self._resolve_distinct_id(info, request)
        token = ctx.bind(
            distinct_id=distinct_id,
            session_key=info.header(SESSION_HEADER) or info.cookies.get(SESSION_COOKIE),
            device_key=info.header(DEVICE_HEADER) or info.cookies.get(DEVICE_COOKIE),
            url=info.url or info.path,
            browser=info.user_agent,
        )
        state: Dict[str, Any] = {"token": token, "started": time.monotonic(), "info": info}

        wants_session = self.track_sessions if track_sessions is None else track_sessions
        if wants_session and not self.ignored(info.path):
            try:
                self.client.sessions.track(entry_url=info.url or info.path)
            except Exception as exc:
                logger.debug("nexus: session track failed: %s", exc)
        return state

    def finish(
        self,
        state: Mapping[str, Any],
        *,
        status: Optional[int] = None,
        exc: Optional[BaseException] = None,
        request: Any = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Record the outcome and unbind the context. Always safe to call."""
        info: RequestInfo = state.get("info") or RequestInfo()
        started = state.get("started")
        duration_ms = int((time.monotonic() - started) * 1000) if started else None

        try:
            if exc is not None and self.capture_errors:
                self.capture(exc, info, status=status)
            if self.track_requests and not self.ignored(info.path) and self._sampled():
                properties: Dict[str, Any] = {
                    "method": info.method,
                    "path": info.route or info.path,
                }
                if status is not None:
                    properties["status"] = status
                if duration_ms is not None:
                    properties["duration_ms"] = duration_ms
                if self.record_query and info.query:
                    properties["query"] = info.query
                if extra:
                    properties.update(extra)
                self.client.events.track(self.event_name, properties)
        except Exception as error:  # telemetry must never break a response
            logger.warning("nexus: request telemetry failed: %s", error)
        finally:
            token = state.get("token")
            if token is not None:
                ctx.unbind(token)

    def capture(
        self, exc: BaseException, info: Optional[RequestInfo] = None, *, status: Optional[int] = None
    ) -> None:
        """Report an exception with request context attached."""
        context: Dict[str, Any] = {}
        if info is not None:
            context.update({"method": info.method, "path": info.route or info.path})
            if info.client_ip:
                context["ip"] = info.client_ip
        if status is not None:
            context["status"] = status
        try:
            self.client.errors.capture_exception(
                exc,
                handled=False,
                level="error",
                url=(info.url or info.path) if info else None,
                context=context,
            )
        except Exception as error:  # pragma: no cover - last-ditch
            logger.warning("nexus: error capture failed: %s", error)

    # ── internals ────────────────────────────────────────────────────────────

    def _sampled(self) -> bool:
        return self.sample_rate >= 1.0 or random.random() < self.sample_rate

    def _resolve_distinct_id(self, info: RequestInfo, request: Any) -> Optional[str]:
        if self.distinct_id_resolver is not None and request is not None:
            try:
                resolved = self.distinct_id_resolver(request)
                if resolved:
                    return str(resolved)
            except Exception as exc:
                logger.debug("nexus: distinct_id_resolver failed: %s", exc)
        return info.header(DISTINCT_HEADER)


def client_ip_from(headers: Mapping[str, str], fallback: Optional[str] = None) -> Optional[str]:
    """Best-effort client IP, honouring ``X-Forwarded-For``."""
    for name in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        for key, value in headers.items():
            if key.lower() == name and value:
                return value.split(",")[0].strip()
    return fallback


def parse_cookies(raw: Optional[str]) -> Dict[str, str]:
    """Parse a ``Cookie`` header without pulling in http.cookies' quirks."""
    cookies: Dict[str, str] = {}
    if not raw:
        return cookies
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies[name.strip()] = value.strip()
    return cookies


def resolve_client(client: Any = None, **init_options: Any) -> Any:
    """Return ``client``, else the default one, else initialise from the env."""
    if client is not None:
        return client
    from .._default import get_client, init, is_initialized

    if is_initialized():
        return get_client()
    return init(**init_options)


def iter_header_pairs(raw: Iterable[Any]) -> Dict[str, str]:
    """Normalise ASGI's ``[(b"name", b"value"), ...]`` to a str dict."""
    headers: Dict[str, str] = {}
    for name, value in raw:
        key = name.decode("latin-1") if isinstance(name, bytes) else str(name)
        val = value.decode("latin-1") if isinstance(value, bytes) else str(value)
        headers[key.lower()] = val
    return headers
