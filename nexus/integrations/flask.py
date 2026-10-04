"""Flask integration.

::

    from flask import Flask
    from nexus.integrations.flask import NexusFlask

    app = Flask(__name__)
    app.config["NEXUS_API_KEY"] = "nxs_live_xxx"
    nexus_ext = NexusFlask(app)

    @app.get("/orders/<int:order_id>")
    def show(order_id):
        nexus_ext.client.events.track("order_viewed", {"id": order_id})
        return {"ok": True}

Config keys (all optional except the first):

====================================  ==========================================
``NEXUS_API_KEY``                     Tenant key (or set ``NEXUS_API_KEY`` in the env)
``NEXUS_BASE_URL``                    API origin
``NEXUS_TRACK_REQUESTS``              Event per request (default ``True``)
``NEXUS_TRACK_SESSIONS``              Also refresh the Nexus session (default ``False``)
``NEXUS_CAPTURE_ERRORS``              Report unhandled exceptions (default ``True``)
``NEXUS_LOGGING``                     Attach :class:`nexus.NexusLogHandler` (default ``False``)
``NEXUS_LOG_LEVEL``                   Level for that handler (default ``INFO``)
``NEXUS_EVENT_NAME``                  Request event name (default ``http_request``)
``NEXUS_SAMPLE_RATE``                 Fraction of requests tracked (default ``1.0``)
``NEXUS_IGNORE_PATHS``                Path prefixes to skip
====================================  ==========================================

By default the current user is read from ``flask_login``'s ``current_user`` when
it is installed; pass ``distinct_id_resolver=fn(flask_request)`` to change that.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from .._logging import logger
from ._common import RequestInfo, RequestTracker, client_ip_from, resolve_client

__all__ = ["NexusFlask", "default_distinct_id"]


def default_distinct_id(request: Any) -> Optional[str]:
    """Read the logged-in user from flask_login, when it is installed."""
    try:
        from flask_login import current_user  # type: ignore
    except Exception:
        return None
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            return str(getattr(current_user, "id", None) or current_user.get_id())
    except Exception:  # pragma: no cover - outside a request context
        return None
    return None


class NexusFlask:
    """Flask extension wiring Nexus into the request lifecycle."""

    def __init__(
        self,
        app: Any = None,
        client: Any = None,
        *,
        api_key: Optional[str] = None,
        distinct_id_resolver: Optional[Callable[[Any], Optional[str]]] = None,
        **tracker_options: Any,
    ) -> None:
        self.client = client
        self.tracker: Optional[RequestTracker] = None
        self._api_key = api_key
        self._distinct_id_resolver = distinct_id_resolver or default_distinct_id
        self._tracker_options = tracker_options
        self._log_handler: Optional[logging.Handler] = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> None:
        """Attach to a Flask app (supports the application-factory pattern)."""
        config = app.config
        options: Dict[str, Any] = {
            "track_requests": config.get("NEXUS_TRACK_REQUESTS", True),
            "track_sessions": config.get("NEXUS_TRACK_SESSIONS", False),
            "capture_errors": config.get("NEXUS_CAPTURE_ERRORS", True),
            "event_name": config.get("NEXUS_EVENT_NAME", "http_request"),
            "sample_rate": config.get("NEXUS_SAMPLE_RATE", 1.0),
        }
        if config.get("NEXUS_IGNORE_PATHS") is not None:
            options["ignore_paths"] = config["NEXUS_IGNORE_PATHS"]
        options.update(self._tracker_options)

        if self.client is None:
            init_kwargs: Dict[str, Any] = {}
            api_key = self._api_key or config.get("NEXUS_API_KEY")
            if api_key:
                init_kwargs["api_key"] = api_key
            if config.get("NEXUS_BASE_URL"):
                init_kwargs["base_url"] = config["NEXUS_BASE_URL"]
            if config.get("NEXUS_APP_VERSION"):
                init_kwargs["app_version"] = config["NEXUS_APP_VERSION"]
            if config.get("NEXUS_RELEASE"):
                init_kwargs["release"] = config["NEXUS_RELEASE"]
            self.client = resolve_client(None, **init_kwargs)

        self.tracker = RequestTracker(
            self.client, distinct_id_resolver=self._distinct_id_resolver, **options
        )

        app.extensions = getattr(app, "extensions", {})
        app.extensions["nexus"] = self

        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.teardown_request(self._teardown_request)

        if config.get("NEXUS_LOGGING"):
            from ..logging_handler import NexusLogHandler

            level = config.get("NEXUS_LOG_LEVEL", logging.INFO)
            if isinstance(level, str):
                level = getattr(logging, level.upper(), logging.INFO)
            self._log_handler = NexusLogHandler(self.client, level=level)
            app.logger.addHandler(self._log_handler)

        # Unhandled exceptions: Flask fires this before the 500 is rendered.
        if options["capture_errors"]:
            try:
                from flask import got_request_exception

                got_request_exception.connect(self._on_exception, app, weak=False)
            except Exception as exc:  # pragma: no cover - blinker missing
                logger.debug("nexus: could not connect got_request_exception: %s", exc)

    # ── hooks ────────────────────────────────────────────────────────────────

    def _info(self, request: Any) -> RequestInfo:
        headers = {k.lower(): v for k, v in request.headers.items()}
        route = None
        if getattr(request, "url_rule", None) is not None:
            route = request.url_rule.rule
        return RequestInfo(
            method=request.method,
            path=request.path,
            route=route,
            url=request.base_url,
            headers=headers,
            cookies=dict(request.cookies or {}),
            client_ip=client_ip_from(headers, request.remote_addr),
            query=request.query_string.decode("latin-1") if request.query_string else None,
        )

    def _before_request(self) -> None:
        from flask import g, request

        if self.tracker is None:
            return
        g._nexus_state = self.tracker.begin(self._info(request), request)

    def _after_request(self, response: Any) -> Any:
        from flask import g, request

        state = getattr(g, "_nexus_state", None)
        if state is not None and self.tracker is not None:
            # The route is only known after dispatch — refresh it.
            if getattr(request, "url_rule", None) is not None:
                state["info"].route = request.url_rule.rule
            state["_status"] = response.status_code
        return response

    def _teardown_request(self, exc: Optional[BaseException] = None) -> None:
        from flask import g

        state = getattr(g, "_nexus_state", None)
        if state is None or self.tracker is None:
            return
        g._nexus_state = None
        # got_request_exception already reported it; don't double-report here.
        self.tracker.finish(state, status=state.get("_status", 500 if exc else None))

    def _on_exception(self, sender: Any, exception: BaseException, **extra: Any) -> None:
        from flask import request

        if self.tracker is None:
            return
        try:
            self.tracker.capture(exception, self._info(request))
        except Exception as error:  # pragma: no cover - last-ditch
            logger.warning("nexus: flask error capture failed: %s", error)
