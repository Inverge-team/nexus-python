"""Celery integration — task telemetry and a queued dispatcher.

Two independent pieces:

**1. Task telemetry** — one call wires Celery's signals so every task run is a
Nexus event and every failure a Nexus error::

    from celery import Celery
    from nexus.integrations.celery import install_celery

    app = Celery("worker")
    install_celery(app, api_key="nxs_live_xxx")

**2. A queued dispatcher** — push Nexus delivery onto your own Celery queue so a
web request never waits on it::

    from nexus.integrations.celery import CeleryDispatcher, nexus_delivery_task

    delivery = nexus_delivery_task(app)                  # registers the task
    queued = client.with_dispatcher(CeleryDispatcher(delivery))
    queued.events.track("order_placed")                  # returns immediately

The client already batches telemetry on a background thread, so the dispatcher
is for teams that want *all* delivery on their broker, with its retries and
visibility.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .. import context as ctx
from .._logging import logger
from .._payloads import Request
from ..dispatcher import Dispatcher
from ._common import resolve_client

__all__ = ["install_celery", "CeleryDispatcher", "nexus_delivery_task", "header_context"]


def install_celery(
    celery_app: Any = None,
    client: Any = None,
    *,
    api_key: Optional[str] = None,
    track_tasks: bool = True,
    capture_errors: bool = True,
    event_name: str = "celery_task",
    flush_on_shutdown: bool = True,
) -> Any:
    """Connect Nexus to Celery's signals. Returns the client in use.

    Args:
        celery_app: Only used for documentation/symmetry — the signals are
            global, so passing it is optional.
        client: An existing :class:`nexus.NexusClient`.
        api_key: Build a client with this key when none is given.
        track_tasks: Event per task with runtime and success/failure.
        capture_errors: Report task exceptions as Nexus errors.
        event_name: Name of the per-task event.
        flush_on_shutdown: Flush buffered telemetry when a worker stops.
    """
    from celery import signals

    resolved = resolve_client(client, api_key=api_key) if (client or api_key) else resolve_client()
    starts: Dict[str, float] = {}
    tokens: Dict[str, Any] = {}

    def on_prerun(task_id: str = "", task: Any = None, **kwargs: Any) -> None:
        import time

        starts[task_id] = time.monotonic()
        # A task launched from a web request can carry the user through its
        # headers; bind whatever we find so the work stays on one timeline.
        headers = getattr(getattr(task, "request", None), "headers", None) or {}
        tokens[task_id] = ctx.bind(
            distinct_id=headers.get("nexus-distinct-id"),
            session_key=headers.get("nexus-session-key"),
        )

    def on_postrun(task_id: str = "", task: Any = None, state: Optional[str] = None, **kwargs: Any) -> None:
        import time

        started = starts.pop(task_id, None)
        token = tokens.pop(task_id, None)
        try:
            if track_tasks:
                properties: Dict[str, Any] = {
                    "task": getattr(task, "name", None),
                    "state": state,
                }
                if started is not None:
                    properties["duration_ms"] = int((time.monotonic() - started) * 1000)
                resolved.events.track(event_name, properties)
        except Exception as exc:
            logger.warning("nexus: celery task telemetry failed: %s", exc)
        finally:
            if token is not None:
                ctx.unbind(token)

    def on_failure(task_id: str = "", exception: Optional[BaseException] = None, sender: Any = None, **kwargs: Any) -> None:
        if not capture_errors or exception is None:
            return
        try:
            resolved.errors.capture_exception(
                exception,
                handled=False,
                level="error",
                context={"task": getattr(sender, "name", None), "task_id": task_id},
            )
        except Exception as exc:
            logger.warning("nexus: celery error capture failed: %s", exc)

    def on_shutdown(**kwargs: Any) -> None:
        try:
            resolved.flush(timeout=5.0)
        except Exception as exc:  # pragma: no cover - shutdown path
            logger.debug("nexus: celery shutdown flush failed: %s", exc)

    signals.task_prerun.connect(on_prerun, weak=False)
    signals.task_postrun.connect(on_postrun, weak=False)
    signals.task_failure.connect(on_failure, weak=False)
    if flush_on_shutdown:
        signals.worker_shutdown.connect(on_shutdown, weak=False)
    return resolved


class CeleryDispatcher(Dispatcher):
    """Delivers partner-API requests through a Celery task.

    Build the task with :func:`nexus_delivery_task` (or supply your own
    callable with a ``.delay(method, path, json, headers)`` signature).
    """

    def __init__(self, task: Any, *, queue: Optional[str] = None) -> None:
        self.task = task
        self.queue = queue

    def dispatch(self, request: Request) -> Any:
        options: Dict[str, Any] = {"queue": self.queue} if self.queue else {}
        self.task.apply_async(
            args=[request.method, request.path, request.json, dict(request.headers)], **options
        )
        return None


def nexus_delivery_task(celery_app: Any, client: Any = None, *, name: str = "nexus.deliver") -> Any:
    """Register and return the Celery task that performs the HTTP delivery."""
    resolved = client

    @celery_app.task(name=name, bind=True, max_retries=3, ignore_result=True)
    def deliver(self: Any, method: str, path: str, json: Any = None, headers: Any = None) -> None:
        target = resolved
        if target is None:
            from .._default import get_client

            target = get_client()
        try:
            target.request(Request(method, path, json, headers or {}))
        except Exception as exc:  # pragma: no cover - broker retry path
            raise self.retry(exc=exc, countdown=min(60, 2 ** self.request.retries))

    return deliver


def header_context() -> Dict[str, str]:
    """Headers to attach when queueing a task, so it inherits the user.

    ::

        my_task.apply_async(args=[...], headers=header_context())
    """
    current = ctx.current()
    headers: Dict[str, str] = {}
    if current.distinct_id:
        headers["nexus-distinct-id"] = current.distinct_id
    if current.session_key:
        headers["nexus-session-key"] = current.session_key
    return headers
