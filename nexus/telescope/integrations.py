"""Telescope for Python web frameworks and task queues.

- :class:`TelescopeWSGIMiddleware` — any WSGI app (Flask, Pyramid, Bottle, Falcon…)
- :class:`TelescopeASGIMiddleware` — any ASGI app (FastAPI, Starlette, Quart, Litestar…)
- :class:`DjangoTelescopeMiddleware` — Django (plus ORM queries and model signals)
- :func:`instrument_flask` — names Flask routes and captures their exceptions
- :func:`instrument_celery` — Celery tasks as jobs that join the dispatching trace
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl

from ._recorder import Recorder
from ._redact import safe
from .instrument import _body

ROUTE_KEY = "nexus.telescope.route"
USER_KEY = "nexus.telescope.user"


def _environ_headers(environ: Dict[str, Any]) -> Dict[str, str]:
    h = {k[5:].replace("_", "-").lower(): str(v) for k, v in environ.items() if k.startswith("HTTP_")}
    for key, name in (("CONTENT_TYPE", "content-type"), ("CONTENT_LENGTH", "content-length")):
        if environ.get(key):
            h[name] = str(environ[key])
    return h


class TelescopeWSGIMiddleware:
    """``app.wsgi_app = TelescopeWSGIMiddleware(app.wsgi_app, telescope)``."""

    def __init__(self, app: Callable[..., Iterable[bytes]], telescope: Any = None) -> None:
        from . import get

        self.app = app
        self.rec: Recorder = (telescope or get()).recorder

    def __call__(self, environ: Dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        path = environ.get("PATH_INFO") or "/"
        if not self.rec.enabled() or self.rec.ignores_path(path):
            return self.app(environ, start_response)
        # A separate generator: a `return` inside a generator would send an empty body.
        return self._recorded(environ, start_response, path)

    def _recorded(self, environ: Dict[str, Any], start_response: Callable[..., Any], path: str) -> Iterable[bytes]:
        rec = self.rec
        headers = _environ_headers(environ)
        host = headers.get("host") or environ.get("SERVER_NAME", "")
        qs = environ.get("QUERY_STRING") or ""
        batch = rec.begin(
            "request",
            {
                "method": environ.get("REQUEST_METHOD", "GET"),
                "uri": "{0}://{1}{2}{3}".format(environ.get("wsgi.url_scheme", "http"), host, path, "?" + qs if qs else ""),
                "path": path,
                "ip": (headers.get("x-forwarded-for") or environ.get("REMOTE_ADDR") or "").split(",")[0].strip(),
                "headers": headers,
                "query": dict(parse_qsl(qs)) or None,
            },
            traceparent=headers.get("traceparent"),
        )
        if batch is None:
            yield from self.app(environ, start_response)
            return
        token = rec.activate(batch)
        status: List[str] = ["500"]
        resp_headers: Dict[str, str] = {}
        body: List[bytes] = []
        size = [0]

        def _start_response(s: str, h: List[Any], exc_info: Any = None) -> Any:
            status[0] = s.split(" ", 1)[0]
            resp_headers.update({k.lower(): v for k, v in h})
            return start_response(s, h, exc_info) if exc_info is not None else start_response(s, h)

        try:
            result = self.app(environ, _start_response)
            for chunk in result:
                if rec.capture_bodies and size[0] <= rec.body_size_limit * 1024:
                    size[0] += len(chunk)
                    body.append(chunk)
                yield chunk
            if hasattr(result, "close"):
                result.close()
        except BaseException as exc:
            rec.exception(exc, handled=False)
            raise
        finally:
            rec.end(
                batch,
                {
                    "responseStatus": int(status[0]) if status[0].isdigit() else status[0],
                    "responseHeaders": resp_headers,
                    "route": environ.get(ROUTE_KEY),
                    "response": _body(rec, resp_headers.get("content-type", ""), b"".join(body)) if size[0] <= rec.body_size_limit * 1024 else "Response too large — not recorded",
                },
                status[0],
            )
            rec.deactivate(token)


class TelescopeASGIMiddleware:
    """``app.add_middleware(TelescopeASGIMiddleware)`` (FastAPI/Starlette) or ``app = TelescopeASGIMiddleware(app)``."""

    def __init__(self, app: Any, telescope: Any = None) -> None:
        from . import get

        self.app = app
        self.rec: Recorder = (telescope or get()).recorder

    async def __call__(self, scope: Dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        rec = self.rec
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or "/"
        if not rec.enabled() or rec.ignores_path(path):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        qs = (scope.get("query_string") or b"").decode("latin-1")
        client = scope.get("client") or ("", 0)
        batch = rec.begin(
            "request",
            {
                "method": scope.get("method"),
                "uri": "{0}://{1}{2}{3}".format(scope.get("scheme", "http"), headers.get("host", ""), path, "?" + qs if qs else ""),
                "path": path,
                "ip": (headers.get("x-forwarded-for") or client[0] or "").split(",")[0].strip(),
                "headers": headers,
                "query": dict(parse_qsl(qs)) or None,
            },
            traceparent=headers.get("traceparent"),
        )
        if batch is None:
            await self.app(scope, receive, send)
            return
        token = rec.activate(batch)
        state: Dict[str, Any] = {"status": 500, "headers": {}, "body": [], "size": 0}
        req_body: List[bytes] = []

        async def _receive() -> Any:
            msg = await receive()
            if msg.get("type") == "http.request" and rec.capture_bodies and sum(len(b) for b in req_body) < rec.body_size_limit * 1024:
                req_body.append(msg.get("body") or b"")
            return msg

        async def _send(msg: Dict[str, Any]) -> None:
            if msg["type"] == "http.response.start":
                state["status"] = msg["status"]
                state["headers"] = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in msg.get("headers") or []}
            elif msg["type"] == "http.response.body" and rec.capture_bodies:
                chunk = msg.get("body") or b""
                state["size"] += len(chunk)
                if state["size"] <= rec.body_size_limit * 1024:
                    state["body"].append(chunk)
            await send(msg)

        try:
            await self.app(scope, _receive, _send)
        except BaseException as exc:
            rec.exception(exc, handled=False)
            raise
        finally:
            route = scope.get("route")
            req_type = headers.get("content-type", "")
            rec.end(
                batch,
                {
                    "responseStatus": state["status"],
                    "responseHeaders": state["headers"],
                    "route": getattr(route, "path", None) or scope.get(ROUTE_KEY),
                    "payload": _body(rec, req_type, b"".join(req_body)) if req_body and "multipart" not in req_type else None,
                    "response": _body(rec, state["headers"].get("content-type", ""), b"".join(state["body"])) if state["size"] <= rec.body_size_limit * 1024 else "Response too large — not recorded",
                },
                str(state["status"]),
            )
            rec.deactivate(token)


# ── Django ───────────────────────────────────────────────────────────────────


class DjangoTelescopeMiddleware:
    """Add ``"nexus.telescope.integrations.DjangoTelescopeMiddleware"`` FIRST in
    ``MIDDLEWARE``. Records the request, every ORM query (with N+1 detection),
    model saves/deletes, exceptions and logs. Configure with ``NEXUS = {...}``
    in settings (``TELESCOPE`` sub-dict for Telescope options)."""

    _installed = False

    def __init__(self, get_response: Callable[[Any], Any]) -> None:
        from . import get, init

        self.get_response = get_response
        tel = get()
        if tel is None:
            from django.conf import settings  # type: ignore

            conf = dict(getattr(settings, "NEXUS", {}) or {})
            options = dict(conf.get("TELESCOPE", {}) or {})
            tel = init(api_key=conf.get("API_KEY"), base_url=conf.get("BASE_URL"), **{k.lower(): v for k, v in options.items()})
        self.rec: Recorder = tel.recorder
        if not DjangoTelescopeMiddleware._installed:
            DjangoTelescopeMiddleware._installed = True
            _install_django(self.rec)

    def __call__(self, request: Any) -> Any:
        rec = self.rec
        path = request.path_info or "/"
        if not rec.enabled() or rec.ignores_path(path):
            return self.get_response(request)
        meta = request.META
        headers = {k.lower(): v for k, v in request.headers.items()}
        batch = rec.begin(
            "request",
            {
                "method": request.method,
                "uri": request.build_absolute_uri(),
                "path": path,
                "ip": (meta.get("HTTP_X_FORWARDED_FOR") or meta.get("REMOTE_ADDR") or "").split(",")[0].strip(),
                "headers": headers,
                "query": dict(request.GET.items()) or None,
            },
            traceparent=headers.get("traceparent"),
            started_at=time.time(),
        )
        if batch is None:
            return self.get_response(request)
        token = rec.activate(batch)
        response = None
        try:
            response = self.get_response(request)
            return response
        finally:
            try:
                self._finish(request, response, batch)
            finally:
                rec.deactivate(token)

    def process_exception(self, request: Any, exception: BaseException) -> None:
        self.rec.exception(exception, handled=False)
        return None

    def _finish(self, request: Any, response: Any, batch: Any) -> None:
        rec = self.rec
        match = getattr(request, "resolver_match", None)
        route = getattr(match, "route", None)
        content: Dict[str, Any] = {
            "route": "/" + route.lstrip("^").rstrip("$") if route else None,
            "controller": getattr(match, "_func_path", None) or getattr(match, "view_name", None),
            "routeName": getattr(match, "view_name", None),
        }
        try:
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                ctype = request.content_type or ""
                content["payload"] = dict(request.POST.items()) if "form" in ctype else _body(rec, ctype, request.body)
        except Exception:
            pass
        user = getattr(request, "user", None)
        try:
            if user is not None and getattr(user, "is_authenticated", False):
                batch.user = {"id": str(user.pk), "email": str(getattr(user, "email", "") or "") or None, "name": str(user.get_username())}
                batch.user = {k: v for k, v in batch.user.items() if v}
        except Exception:
            pass
        if response is not None:
            ctype = response.get("Content-Type", "") if hasattr(response, "get") else ""
            content["responseStatus"] = response.status_code
            content["responseHeaders"] = {k.lower(): v for k, v in response.items()} if hasattr(response, "items") else None
            if not getattr(response, "streaming", False):
                content["response"] = _body(rec, ctype, getattr(response, "content", None))
            status: Optional[str] = str(response.status_code)
        else:
            content["responseStatus"] = 500
            status = "500"
        rec.end(batch, {k: v for k, v in content.items() if v is not None}, status)


def _install_django(rec: Recorder) -> None:
    from django.core.signals import got_request_exception  # type: ignore
    from django.db.backends.signals import connection_created  # type: ignore
    from django.db import connections  # type: ignore
    from django.db.models.signals import post_delete, post_save  # type: ignore

    from .instrument import django_execute_wrapper

    wrapper = django_execute_wrapper(rec)

    def attach(connection: Any, **_: Any) -> None:
        if wrapper not in connection.execute_wrappers:
            connection.execute_wrappers.append(wrapper)

    connection_created.connect(attach, weak=False)
    for conn in connections.all(initialized_only=True) if _accepts_initialized_only(connections) else []:
        attach(conn)

    def saved(sender: Any, instance: Any, created: bool, **_: Any) -> None:
        if rec.current() is not None:
            rec.record("model", {"action": "created" if created else "updated", "model": "{0}.{1}".format(sender.__module__, sender.__name__), "key": safe(getattr(instance, "pk", None))}, status="created" if created else "updated")

    def deleted(sender: Any, instance: Any, **_: Any) -> None:
        if rec.current() is not None:
            rec.record("model", {"action": "deleted", "model": "{0}.{1}".format(sender.__module__, sender.__name__), "key": safe(getattr(instance, "pk", None))}, status="deleted")

    post_save.connect(saved, weak=False)
    post_delete.connect(deleted, weak=False)

    def failed(sender: Any, request: Any = None, **_: Any) -> None:
        import sys

        exc = sys.exc_info()[1]
        if exc is not None and rec.current() is not None and not any(e["type"] == "exception" and e["content"].get("message") == str(exc) for e in rec.current()[0].entries):  # type: ignore[index]
            rec.exception(exc, handled=False)

    got_request_exception.connect(failed, weak=False)


def _accepts_initialized_only(connections: Any) -> bool:
    try:
        connections.all(initialized_only=True)
        return True
    except TypeError:  # Django < 4.1
        return False


# ── Flask ────────────────────────────────────────────────────────────────────


def instrument_flask(app: Any, telescope: Any = None) -> Any:
    """``instrument_flask(app)``: wraps ``app.wsgi_app`` and adds route names,
    the parsed body, the user (flask-login) and exception capture."""
    from flask import got_request_exception, request  # type: ignore

    from . import get

    rec: Recorder = (telescope or get()).recorder
    app.wsgi_app = TelescopeWSGIMiddleware(app.wsgi_app, telescope or get())

    @app.before_request
    def _route() -> None:
        rule = getattr(request, "url_rule", None)
        if rule is not None:
            request.environ[ROUTE_KEY] = rule.rule
        cur = rec.current()
        if cur is not None and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
            cur[0].content["payload"] = rec.redactor().redact(safe(data))
        try:
            from flask_login import current_user  # type: ignore

            if current_user and current_user.is_authenticated:
                rec.user(id=current_user.get_id())
        except Exception:
            pass

    def _failed(sender: Any, exception: BaseException, **_: Any) -> None:
        rec.exception(exception, handled=False)

    got_request_exception.connect(_failed, app, weak=False)
    return app


# ── Celery ───────────────────────────────────────────────────────────────────


def instrument_celery(telescope: Any = None) -> None:
    """Celery tasks as Telescope jobs. Call once, e.g. in your ``celery.py``.

    Publishing a task records a ``job`` entry in the current journey and puts a
    ``traceparent`` in the message headers; the worker's run joins that trace.
    """
    from celery import signals  # type: ignore

    from ._recorder import new_span_id
    from . import get

    tel = telescope or get()
    rec: Recorder = tel.recorder
    tokens: Dict[str, Any] = {}

    @signals.before_task_publish.connect(weak=False)
    def _publish(sender: Any = None, headers: Any = None, body: Any = None, **kw: Any) -> None:
        if rec.current() is None or headers is None:
            return
        span = new_span_id()
        headers["nexus_traceparent"] = rec.traceparent(span)
        rec.record("job", {"name": sender, "queue": kw.get("routing_key"), "status": "dispatched", "jobId": headers.get("id")}, id=span, status="dispatched")

    @signals.task_prerun.connect(weak=False)
    def _prerun(task_id: str = "", task: Any = None, args: Any = None, kwargs: Any = None, **_: Any) -> None:
        req = getattr(task, "request", None)
        tp = getattr(req, "nexus_traceparent", None) or (getattr(req, "headers", None) or {}).get("nexus_traceparent")
        batch = rec.begin(
            "job",
            {"name": getattr(task, "name", "task"), "queue": (getattr(req, "delivery_info", None) or {}).get("routing_key"), "jobId": task_id, "attempts": (getattr(req, "retries", 0) or 0) + 1, "data": {"args": safe(args), "kwargs": safe(kwargs)}},
            traceparent=tp,
            name=getattr(task, "name", None),
        )
        if batch is not None:
            tokens[task_id] = (batch, rec.activate(batch))

    @signals.task_failure.connect(weak=False)
    def _failure(task_id: str = "", exception: Any = None, **_: Any) -> None:
        if task_id in tokens and exception is not None:
            rec.exception(exception, handled=False)

    @signals.task_postrun.connect(weak=False)
    def _postrun(task_id: str = "", state: Any = None, **_: Any) -> None:
        item = tokens.pop(task_id, None)
        if item is None:
            return
        batch, token = item
        status = {"SUCCESS": "processed", "FAILURE": "failed", "RETRY": "released"}.get(str(state), str(state).lower() if state else "processed")
        rec.end(batch, {"status": status}, status)
        rec.deactivate(token)
