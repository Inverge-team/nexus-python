"""The Telescope recorder — the framework-agnostic core.

Context lives in a :class:`contextvars.ContextVar`, so it is correct per
thread (WSGI servers, Celery workers) **and** per asyncio task (ASGI), and a
``with telescope.span(...)`` nests correctly even across ``await``.
"""

from __future__ import annotations

import atexit
import contextvars
import os
import queue
import random
import socket
import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from .. import _payloads as p
from .._logging import logger
from ._redact import Redactor, glob_match, parse_traceparent, safe, status_match

SDK_NAME = "nexus-python"


def new_trace_id() -> str:
    return os.urandom(16).hex()


def new_span_id() -> str:
    return os.urandom(8).hex()


@dataclass
class Batch:
    """One unit of work being recorded — a request, job, command or scheduled task."""

    id: str
    type: str
    trace_id: str
    parent_span_id: Optional[str]
    started_at: float  # epoch seconds
    t0: float  # perf_counter at start
    content: Dict[str, Any]
    name: Optional[str] = None
    status: Optional[str] = None
    duration_ms: float = 0.0
    tags: List[str] = field(default_factory=list)
    user: Optional[Dict[str, Any]] = None
    session_key: Optional[str] = None
    entries: List[Dict[str, Any]] = field(default_factory=list)
    dropped: int = 0
    failed: bool = False
    done: bool = False

    def offset_ms(self, at: Optional[float] = None) -> float:
        return round(max(0.0, ((at if at is not None else time.time()) - self.started_at) * 1000), 3)

    def is_failure(self) -> bool:
        s = self.status
        if s is None and self.content.get("responseStatus") is not None:
            s = str(self.content["responseStatus"])
        if s is None:
            return False
        if self.type == "request":
            return s.isdigit() and int(s) >= 500
        if self.type == "job":
            return s == "failed"
        if self.type in ("command", "schedule"):
            return s not in ("0", "")
        return False

    def wire(self) -> Dict[str, Any]:
        content = dict(self.content)
        if self.dropped:
            content["droppedEntries"] = self.dropped
        return p.compact(
            {
                "id": self.id,
                "type": self.type,
                "traceId": self.trace_id,
                "parentSpanId": self.parent_span_id,
                "startedAt": round(self.started_at * 1000, 3),
                "durationMs": round(self.duration_ms, 3),
                "name": self.name,
                "status": self.status,
                "content": content,
                "tags": list(dict.fromkeys(self.tags)),
                "user": self.user,
                "sessionKey": self.session_key,
                "entries": self.entries,
            }
        )


_current: "contextvars.ContextVar[Optional[Tuple[Batch, str]]]" = contextvars.ContextVar(
    "nexus_telescope", default=None
)


class Recorder:
    """Records units of work and ships them to ``POST /partner/telescope``.

    Never raises into the host application.
    """

    def __init__(
        self,
        client: Any,
        *,
        service: Optional[str] = None,
        release: Optional[str] = None,
        sample_rate: Optional[float] = None,
        ignore_paths: Sequence[str] = ("/health*", "/healthz", "/favicon.ico", "/static/*"),
        ignore_statuses: Optional[Sequence[Any]] = None,
        ignore_types: Sequence[str] = (),
        redact_keys: Sequence[str] = (),
        capture_bodies: bool = True,
        body_size_limit: int = 64,
        max_entries: int = 2000,
        flush_interval: float = 2.0,
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.service = service or os.environ.get("NEXUS_SERVICE_NAME")
        self.release = release or os.environ.get("NEXUS_RELEASE") or getattr(client.config, "release", None)
        env_rate = os.environ.get("NEXUS_TELESCOPE_SAMPLE_RATE")
        self.sample_rate = sample_rate if sample_rate is not None else (float(env_rate) if env_rate else None)
        self.ignore_paths = list(ignore_paths)
        # ``200``, ``2xx`` or ``301-304``; merged with the console's list.
        if ignore_statuses is None:
            ignore_statuses = [s.strip() for s in os.environ.get("NEXUS_TELESCOPE_IGNORE_STATUSES", "").split(",") if s.strip()]
        self.ignore_statuses = [str(s) for s in ignore_statuses]
        self.ignore_types = list(ignore_types)
        self.redact_keys = list(redact_keys)
        self.capture_bodies = capture_bodies
        self.body_size_limit = body_size_limit
        self.max_entries = max_entries
        self.flush_interval = flush_interval
        self._enabled = enabled
        self.remote: Dict[str, Any] = {}
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=5000)
        self._lock = threading.Lock()
        # Batches the worker has taken off the queue but not delivered yet —
        # flush() must wait for these too, or a caller flushing before exit
        # would return while they are still in flight.
        self._inflight = 0
        self._idle = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._pid = os.getpid()
        self._host = socket.gethostname()
        self._delivering = threading.local()
        atexit.register(self.flush, 3.0)

    # ── state ────────────────────────────────────────────────────────────────

    def enabled(self) -> bool:
        return (
            self._enabled
            and self.client.enabled("telescope")
            and self.remote.get("enabled", True) is not False
        )

    def current(self) -> Optional[Tuple[Batch, str]]:
        c = _current.get()
        return c if c is not None and not c[0].done else None

    def ignores(self, kind: str) -> bool:
        return kind in self.ignore_types or kind in (self.remote.get("ignoreTypes") or [])

    def ignores_path(self, path: str) -> bool:
        return any(glob_match(g, path) for g in [*self.ignore_paths, *(self.remote.get("ignorePaths") or [])])

    def ignores_status(self, status: Any) -> bool:
        """Whether a response status is ignored (``200``, ``2xx``, ``301-304``), locally or from the console."""
        patterns = [*self.ignore_statuses, *(self.remote.get("ignoreStatuses") or [])]
        return any(status_match(str(p), str(status)) for p in patterns)

    def redactor(self) -> Redactor:
        return Redactor([*self.redact_keys, *(self.remote.get("redactKeys") or [])])

    def delivering(self) -> bool:
        return bool(getattr(self._delivering, "on", False))

    # ── units of work ────────────────────────────────────────────────────────

    def begin(
        self,
        kind: str,
        content: Optional[Dict[str, Any]] = None,
        *,
        traceparent: Any = None,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        name: Optional[str] = None,
        tags: Sequence[str] = (),
        started_at: Optional[float] = None,
    ) -> Optional[Batch]:
        """Open a unit of work (not yet current — see :meth:`activate`)."""
        if self.delivering() or not self.enabled() or self.ignores(kind):
            return None
        try:
            tp = parse_traceparent(traceparent)
            parent = self.current()
            now = time.time()
            start = started_at if started_at is not None else now
            return Batch(
                id=new_span_id(),
                type=kind,
                trace_id=trace_id or (tp[0] if tp else None) or (parent[0].trace_id if parent else None) or new_trace_id(),
                parent_span_id=parent_span_id or (tp[1] if tp else None) or (parent[1] if parent else None),
                started_at=start,
                t0=time.perf_counter() - (now - start),
                content=self.redactor().redact(safe(content or {})),
                name=name,
                tags=list(tags),
            )
        except Exception:  # pragma: no cover - never break the app
            return None

    def activate(self, batch: Batch) -> contextvars.Token:
        return _current.set((batch, batch.id))

    def deactivate(self, token: Optional[contextvars.Token]) -> None:
        if token is not None:
            try:
                _current.reset(token)
            except ValueError:  # reset from another context (rare in ASGI)
                _current.set(None)

    def end(self, batch: Optional[Batch], content: Optional[Dict[str, Any]] = None, status: Optional[str] = None, name: Optional[str] = None) -> None:
        if batch is None or batch.done:
            return
        batch.done = True
        try:
            batch.duration_ms = (time.perf_counter() - batch.t0) * 1000
            if content:
                batch.content.update(self.redactor().redact(safe(content)))
            if status is not None:
                batch.status = str(status)
            if name is not None:
                batch.name = name
            # An ignored response status drops the request with every entry,
            # before anything leaves the app.
            response_status = batch.status
            if response_status is None and batch.content.get("responseStatus") is not None:
                response_status = str(batch.content["responseStatus"])
            ignored = batch.type == "request" and response_status is not None and self.ignores_status(response_status)
            if not ignored and self._keep(batch):
                self._enqueue(batch.wire())
        except Exception:  # pragma: no cover
            logger.debug("nexus telescope: could not finish a batch", exc_info=True)

    @contextmanager
    def unit(self, kind: str, name: str, content: Optional[Dict[str, Any]] = None, **ctx: Any) -> Iterator[Optional[Batch]]:
        """``with telescope.unit("command", "reports:daily"):`` — a whole unit of work."""
        batch = self.begin(kind, {"name": name, **(content or {})}, name=name, **ctx)
        if batch is None:
            yield None
            return
        token = self.activate(batch)
        try:
            yield batch
        except BaseException as exc:
            self.exception(exc, handled=False)
            self.end(batch, {"error": str(exc)}, "failed" if kind == "job" else "1")
            raise
        else:
            if not batch.done:
                self.end(batch, {}, "processed" if kind == "job" else "0" if kind in ("command", "schedule") else None)
        finally:
            self.deactivate(token)

    # ── entries ──────────────────────────────────────────────────────────────

    def record(
        self,
        kind: str,
        content: Optional[Dict[str, Any]] = None,
        *,
        duration_ms: Optional[float] = None,
        started_at: Optional[float] = None,
        status: Optional[str] = None,
        name: Optional[str] = None,
        tags: Sequence[str] = (),
        id: Optional[str] = None,
        parent_id: Optional[str] = None,
        ctx: Optional[Tuple[Batch, str]] = None,
    ) -> Optional[str]:
        cur = ctx or self.current()
        if cur is None or self.delivering() or self.ignores(kind):
            return None
        batch, span = cur
        if batch.done:
            return None
        if len(batch.entries) >= self.max_entries:
            batch.dropped += 1
            return None
        try:
            if started_at is None:
                started_at = time.time() - (duration_ms / 1000 if duration_ms else 0)
            entry_id = id or new_span_id()
            entry: Dict[str, Any] = {
                "id": entry_id,
                "parentId": parent_id or span,
                "type": kind,
                "offsetMs": batch.offset_ms(started_at),
                "content": self.redactor().redact(safe(content or {})),
            }
            if duration_ms is not None:
                entry["durationMs"] = round(duration_ms, 3)
            if status is not None:
                entry["status"] = str(status)
            if name is not None:
                entry["name"] = name
            if tags:
                entry["tags"] = list(tags)
            batch.entries.append(entry)
            if kind == "exception" and (content or {}).get("handled") is not True:
                batch.failed = True
            return entry_id
        except Exception:  # pragma: no cover
            return None

    @contextmanager
    def span(self, name: str, content: Optional[Dict[str, Any]] = None, kind: str = "span") -> Iterator[Optional[str]]:
        """``with telescope.span("price lookup"):`` — entries inside nest under it."""
        cur = self.current()
        if cur is None:
            yield None
            return
        start = time.time()
        t0 = time.perf_counter()
        entry_id = self.record(kind, {"name": name, **(content or {})}, name=name, started_at=start)
        if entry_id is None:
            yield None
            return
        entry = cur[0].entries[-1]
        token = _current.set((cur[0], entry_id))
        try:
            yield entry_id
        except BaseException as exc:
            entry["content"]["error"] = str(exc)
            raise
        finally:
            entry["durationMs"] = round((time.perf_counter() - t0) * 1000, 3)
            try:
                _current.reset(token)
            except ValueError:  # pragma: no cover
                _current.set(cur)

    def exception(self, exc: BaseException, handled: bool = False, context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        tb = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
        frames = [{"function": f.name, "filename": f.filename, "lineno": f.lineno or 0} for f in reversed(tb[-60:])]
        last = tb[-1] if tb else None
        return self.record(
            "exception",
            {
                "class": "{0}.{1}".format(type(exc).__module__, type(exc).__qualname__).replace("builtins.", ""),
                "message": str(exc),
                "file": last.filename if last else None,
                "line": last.lineno if last else None,
                "handled": handled,
                "stack": frames,
                "context": context,
            },
        )

    def log(self, level: str, message: str, context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        return self.record("log", {"level": level.lower(), "message": message, "context": context}, status=level.lower())

    def user(self, **user: Any) -> None:
        cur = self.current()
        if cur is not None and user.get("id") is not None:
            cur[0].user = {k: str(v) for k, v in user.items() if v is not None}

    def tag(self, *tags: str) -> None:
        cur = self.current()
        if cur is not None:
            cur[0].tags.extend(tags)

    def traceparent(self, span_id: Optional[str] = None) -> Optional[str]:
        cur = self.current()
        return "00-{0}-{1}-01".format(cur[0].trace_id, span_id or cur[1]) if cur else None

    # ── delivery ─────────────────────────────────────────────────────────────

    def _keep(self, b: Batch) -> bool:
        if b.failed or b.is_failure():
            return True
        if b.duration_ms >= float(self.remote.get("slowRequestMs") or 1000):
            return True
        user_tag = "Auth:{0}".format(b.user["id"]) if b.user and b.user.get("id") else None
        for t in self.remote.get("monitoredTags") or []:
            if t in b.tags or t == user_tag:
                return True
        rate = self.sample_rate if self.sample_rate is not None else self.remote.get("sampleRate", 1.0)
        return float(rate) >= 1.0 or random.random() < float(rate)

    def _enqueue(self, wire: Dict[str, Any]) -> None:
        self._ensure_thread()
        try:
            self._queue.put_nowait(wire)
        except queue.Full:
            logger.debug("nexus telescope: queue full, dropping a journey")

    def _ensure_thread(self) -> None:
        # After a fork (gunicorn, Celery prefork) the worker thread is gone.
        if self._thread is not None and self._thread.is_alive() and self._pid == os.getpid():
            return
        with self._lock:
            if self._pid != os.getpid():
                self._pid = os.getpid()
                self._queue = queue.Queue(maxsize=5000)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="nexus-telescope", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            batch: List[Dict[str, Any]] = []
            try:
                first = self._queue.get(timeout=self.flush_interval)
            except queue.Empty:
                continue
            with self._idle:
                self._inflight += 1
            batch.append(first)
            deadline = time.monotonic() + 0.2
            while len(batch) < 100 and time.monotonic() < deadline:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    time.sleep(0.02)
            try:
                self._send(batch)
            finally:
                for _ in batch:
                    self._queue.task_done()
                with self._idle:
                    self._inflight -= 1
                    self._idle.notify_all()

    def _send(self, batches: List[Dict[str, Any]]) -> None:
        self._delivering.on = True
        try:
            from .._version import __version__

            body = p.compact(
                {
                    "service": self.service,
                    "host": self._host,
                    "release": self.release,
                    "sdk": {"name": SDK_NAME, "version": __version__},
                    "batches": batches,
                }
            )
            res = self.client.request(p.Request("POST", "/partner/telescope", json=body, retry=True))
            if isinstance(res, dict) and isinstance(res.get("config"), dict):
                self.remote = res["config"]
        except Exception as exc:  # delivery must never break the app
            logger.debug("nexus telescope: delivery failed: %s", exc)
        finally:
            self._delivering.on = False

    def flush(self, timeout: Optional[float] = None) -> None:
        """Send everything queued now (also runs at interpreter exit)."""
        pending: List[Dict[str, Any]] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
                self._queue.task_done()
            except queue.Empty:
                break
        for i in range(0, len(pending), 100):
            self._send(pending[i : i + 100])
        with self._idle:
            self._idle.wait_for(lambda: self._inflight == 0, timeout=timeout if timeout is not None else 10.0)


def run_in(ctx: Optional[Tuple[Batch, str]], fn: Callable[[], Any]) -> Any:
    """Run ``fn`` with ``ctx`` as the current context (for callbacks that lost it)."""
    if ctx is None:
        return fn()
    token = _current.set(ctx)
    try:
        return fn()
    finally:
        _current.reset(token)
