"""Auto-instrumentation for common Python libraries.

Each ``instrument_*`` function is idempotent and a no-op when the library is
not installed. :func:`nexus.telescope.init` calls all of them.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Optional

from ._recorder import Recorder, new_span_id, run_in
from ._redact import safe

_PATCHED = "_nexus_telescope_patched"


def _location() -> dict:
    """The app frame that triggered an entry: first frame outside site-packages and the SDK."""
    try:
        frame: Any = sys._getframe(2)
    except ValueError:  # pragma: no cover
        return {}
    cwd = os.getcwd() + os.sep
    while frame is not None:
        f = frame.f_code.co_filename
        if "site-packages" not in f and "/nexus/" not in f.replace("\\", "/") and not f.startswith("<") and "/lib/python" not in f:
            return {"file": f.replace(cwd, ""), "line": frame.f_lineno}
        frame = frame.f_back
    return {}


def _headers(h: Any) -> dict:
    try:
        return {str(k).lower(): str(v) for k, v in dict(h).items()}
    except Exception:
        return {}


def _body(rec: Recorder, content_type: str, raw: Any) -> Any:
    if not rec.capture_bodies or raw is None:
        return None
    try:
        data = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode()
    except Exception:
        return None
    if len(data) > rec.body_size_limit * 1024:
        return "Body over {0} KB — not recorded".format(rec.body_size_limit)
    if "json" in content_type:
        import json

        try:
            return json.loads(data)
        except ValueError:
            pass
    if "html" in content_type:
        return "HTML ({0} bytes)".format(len(data))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return "[binary {0} bytes]".format(len(data))


def _own(rec: Recorder, url: str) -> bool:
    base = str(getattr(rec.client.config, "base_url", "")).rstrip("/")
    return bool(base) and url.startswith(base + "/partner/")


# ── outgoing HTTP ────────────────────────────────────────────────────────────


def instrument_requests(rec: Recorder) -> bool:
    try:
        import requests  # type: ignore
    except ImportError:
        return False
    Session = requests.Session
    if getattr(Session.send, _PATCHED, False):
        return False
    orig = Session.send

    def send(self: Any, request: Any, **kwargs: Any) -> Any:
        cur = rec.current()
        if cur is None or _own(rec, request.url):
            return orig(self, request, **kwargs)
        span = new_span_id()
        request.headers.setdefault("traceparent", rec.traceparent(span))
        start = time.time()
        t0 = time.perf_counter()
        try:
            res = orig(self, request, **kwargs)
        except Exception as exc:
            rec.record("http", {"method": request.method, "url": request.url, "error": str(exc)}, id=span, started_at=start, duration_ms=(time.perf_counter() - t0) * 1000, status="failed", ctx=cur)
            raise
        rec.record(
            "http",
            {
                "method": request.method,
                "url": request.url,
                "requestHeaders": _headers(request.headers),
                "payload": _body(rec, request.headers.get("content-type", ""), request.body),
                "responseStatus": res.status_code,
                "responseHeaders": _headers(res.headers),
                "response": _body(rec, res.headers.get("content-type", ""), res.content if not kwargs.get("stream") else None),
            },
            id=span,
            started_at=start,
            duration_ms=(time.perf_counter() - t0) * 1000,
            status=str(res.status_code),
            ctx=cur,
        )
        return res

    setattr(send, _PATCHED, True)
    Session.send = send  # type: ignore[assignment]
    return True


def instrument_httpx(rec: Recorder) -> bool:
    try:
        import httpx  # type: ignore
    except ImportError:
        return False
    if getattr(httpx.Client.send, _PATCHED, False):
        return False

    def prepare(request: Any):
        cur = rec.current()
        if cur is None or _own(rec, str(request.url)):
            return None, None
        span = new_span_id()
        if "traceparent" not in request.headers:
            request.headers["traceparent"] = rec.traceparent(span)
        return cur, span

    def done(cur: Any, span: str, request: Any, start: float, t0: float, res: Any = None, exc: Any = None) -> None:
        content: dict = {"method": request.method, "url": str(request.url), "requestHeaders": _headers(request.headers)}
        if res is not None:
            content.update(responseStatus=res.status_code, responseHeaders=_headers(res.headers))
        if exc is not None:
            content["error"] = str(exc)
        rec.record("http", content, id=span, started_at=start, duration_ms=(time.perf_counter() - t0) * 1000, status=str(res.status_code) if res is not None else "failed", ctx=cur)

    sync_send = httpx.Client.send

    def send(self: Any, request: Any, **kw: Any) -> Any:
        cur, span = prepare(request)
        if cur is None:
            return sync_send(self, request, **kw)
        start, t0 = time.time(), time.perf_counter()
        try:
            res = sync_send(self, request, **kw)
        except Exception as exc:
            done(cur, span, request, start, t0, exc=exc)
            raise
        done(cur, span, request, start, t0, res=res)
        return res

    async_send = httpx.AsyncClient.send

    async def asend(self: Any, request: Any, **kw: Any) -> Any:
        cur, span = prepare(request)
        if cur is None:
            return await async_send(self, request, **kw)
        start, t0 = time.time(), time.perf_counter()
        try:
            res = await async_send(self, request, **kw)
        except Exception as exc:
            done(cur, span, request, start, t0, exc=exc)
            raise
        done(cur, span, request, start, t0, res=res)
        return res

    setattr(send, _PATCHED, True)
    httpx.Client.send = send  # type: ignore[assignment]
    httpx.AsyncClient.send = asend  # type: ignore[assignment]
    return True


# ── databases ────────────────────────────────────────────────────────────────


def instrument_sqlalchemy(rec: Recorder) -> bool:
    try:
        from sqlalchemy import event  # type: ignore
        from sqlalchemy.engine import Engine  # type: ignore
    except ImportError:
        return False
    if getattr(Engine, _PATCHED, False):
        return False
    setattr(Engine, _PATCHED, True)

    @event.listens_for(Engine, "before_cursor_execute")
    def _before(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        if rec.current() is not None:
            conn.info.setdefault("_nexus_q", []).append((time.time(), time.perf_counter()))

    @event.listens_for(Engine, "after_cursor_execute")
    def _after(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        stack = conn.info.get("_nexus_q")
        if not stack:
            return
        start, t0 = stack.pop()
        rec.record(
            "query",
            {"sql": statement, "bindings": safe(list(parameters) if isinstance(parameters, (list, tuple)) else parameters), "connection": conn.engine.dialect.name, "rows": getattr(cursor, "rowcount", None), **_location()},
            duration_ms=(time.perf_counter() - t0) * 1000,
            started_at=start,
        )

    @event.listens_for(Engine, "handle_error")
    def _error(ctx: Any) -> None:
        stack = ctx.connection.info.get("_nexus_q") if ctx.connection is not None else None
        if stack:
            start, t0 = stack.pop()
            rec.record("query", {"sql": ctx.statement, "connection": ctx.engine.dialect.name, "error": str(ctx.original_exception), **_location()}, duration_ms=(time.perf_counter() - t0) * 1000, started_at=start)

    return True


def django_execute_wrapper(rec: Recorder):
    """A Django ``execute_wrapper`` that records every query (installed on each new connection)."""

    def wrapper(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if rec.current() is None:
            return execute(sql, params, many, context)
        start, t0 = time.time(), time.perf_counter()
        error = None
        try:
            return execute(sql, params, many, context)
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            conn = context.get("connection")
            rec.record(
                "query",
                {"sql": sql, "bindings": safe(list(params) if isinstance(params, (list, tuple)) else params), "connection": getattr(conn, "alias", None), "error": error, **_location()},
                duration_ms=(time.perf_counter() - t0) * 1000,
                started_at=start,
            )

    return wrapper


def instrument_redis(rec: Recorder) -> bool:
    try:
        import redis  # type: ignore
    except ImportError:
        return False
    Redis = redis.Redis
    if getattr(Redis.execute_command, _PATCHED, False):
        return False
    orig = Redis.execute_command

    def execute_command(self: Any, *args: Any, **kwargs: Any) -> Any:
        cur = rec.current()
        if cur is None:
            return orig(self, *args, **kwargs)
        start, t0 = time.time(), time.perf_counter()
        error = None
        try:
            return orig(self, *args, **kwargs)
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            text = " ".join(str(a) if not isinstance(a, bytes) else "<bytes>" for a in args)[:500]
            rec.record("redis", {"command": text, "connection": "redis", "error": error}, duration_ms=(time.perf_counter() - t0) * 1000, started_at=start, ctx=cur)

    setattr(execute_command, _PATCHED, True)
    Redis.execute_command = execute_command  # type: ignore[assignment]
    return True


# ── logs ─────────────────────────────────────────────────────────────────────


class TelescopeLogHandler(logging.Handler):
    """Records log lines into the current journey (and only while one is open)."""

    def __init__(self, rec: Recorder, level: int = logging.INFO) -> None:
        super().__init__(level)
        self.rec = rec

    def emit(self, record: logging.LogRecord) -> None:
        if self.rec.current() is None or self.rec.delivering() or record.name.startswith(("nexus", "urllib3", "httpx", "httpcore")):
            return
        try:
            context = {k: safe(v) for k, v in record.__dict__.items() if k not in _STD_ATTRS and not k.startswith("_")}
            context["logger"] = record.name
            if record.exc_info and record.exc_info[1] is not None:
                self.rec.exception(record.exc_info[1], handled=True, context={"logger": record.name, "message": record.getMessage()})
                return
            self.rec.log(record.levelname, record.getMessage(), context)
        except Exception:  # pragma: no cover
            pass


_STD_ATTRS = set(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {"message", "asctime"}


def instrument_logging(rec: Recorder, level: int = logging.INFO) -> bool:
    root = logging.getLogger()
    if any(isinstance(h, TelescopeLogHandler) for h in root.handlers):
        return False
    root.addHandler(TelescopeLogHandler(rec, level))
    return True


def capture_in(ctx: Any, fn: Any) -> Any:
    """Re-enter a recorded context inside a callback (e.g. a thread pool)."""
    return run_in(ctx, fn)
