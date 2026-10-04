"""The Django middleware: per-request context, events and error capture."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .._common import RequestInfo, RequestTracker, client_ip_from, resolve_client

__all__ = ["NexusMiddleware", "default_distinct_id"]


def default_distinct_id(request: Any) -> Optional[str]:
    """``request.user.pk`` for an authenticated user, else ``None``."""
    user = getattr(request, "user", None)
    if user is None:
        return None
    try:
        if getattr(user, "is_authenticated", False):
            return str(getattr(user, "pk", None) or user.get_username())
    except Exception:  # pragma: no cover - exotic user models
        return None
    return None


class NexusMiddleware:
    """Add ``"nexus.integrations.django.NexusMiddleware"`` to ``MIDDLEWARE``.

    Put it high in the list (just under ``SecurityMiddleware``) so it wraps as
    much of the stack as possible, but **below** the authentication middleware
    if you want ``request.user`` resolved for identity.
    """

    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        from ...exceptions import ConfigurationError
        from .conf import client_options, django_settings, tracker_options

        self.get_response = get_response
        values = django_settings()
        try:
            self.client = resolve_client(None, **client_options(values))
        except ConfigurationError as exc:
            raise ConfigurationError(
                "NexusMiddleware is in MIDDLEWARE but no API key is configured. "
                "Set NEXUS = {'API_KEY': ...} in settings, or export NEXUS_API_KEY."
            ) from exc
        self.tracker = RequestTracker(
            self.client, distinct_id_resolver=default_distinct_id, **tracker_options(values)
        )

    def __call__(self, request: Any) -> Any:
        info = self._info(request)
        state = self.tracker.begin(info, request)
        try:
            response = self.get_response(request)
        except Exception as exc:
            # process_exception already reported it when it ran; capture here
            # covers exceptions raised by middleware below us.
            self.tracker.finish(
                state, status=500, exc=None if getattr(request, "_nexus_reported", False) else exc
            )
            raise
        info.route = self._route(request) or info.route
        self.tracker.finish(state, status=getattr(response, "status_code", None), request=request)
        return response

    def process_exception(self, request: Any, exception: BaseException) -> None:
        """Report view exceptions with the resolved route attached."""
        if not self.tracker.capture_errors:
            return None
        request._nexus_reported = True
        info = self._info(request)
        info.route = self._route(request) or info.route
        self.tracker.capture(exception, info)
        return None

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _route(request: Any) -> Optional[str]:
        match = getattr(request, "resolver_match", None)
        return getattr(match, "route", None) if match is not None else None

    def _info(self, request: Any) -> RequestInfo:
        headers = {
            key[5:].replace("_", "-").lower(): str(value)
            for key, value in request.META.items()
            if key.startswith("HTTP_")
        }
        if request.META.get("CONTENT_TYPE"):
            headers["content-type"] = request.META["CONTENT_TYPE"]
        return RequestInfo(
            method=request.method,
            path=request.path,
            route=self._route(request),
            url=request.build_absolute_uri(request.path),
            headers=headers,
            cookies=dict(getattr(request, "COOKIES", {}) or {}),
            client_ip=client_ip_from(headers, request.META.get("REMOTE_ADDR")),
            query=request.META.get("QUERY_STRING") or None,
        )
