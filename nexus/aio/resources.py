"""Async mirrors of the resource namespaces.

Each method matches its synchronous counterpart in :mod:`nexus.resources` —
same name, same arguments, same return value — you just ``await`` it. The
request bodies come from the shared :mod:`nexus._payloads`, so the two clients
can never drift apart on endpoints or field names.

The fluent builders are shared too: on the async client
``await client.push.notification().title("Hi").to_user("u_1").send()``.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from .. import _payloads as p
from .._logging import logger
from .._payloads import RoomMessage
from ..builders.live_activity import LiveActivityBuilder
from ..builders.push import PushBuilder
from ..exceptions import NexusError
from ..models import FlagEvaluation, RemoteConfigSnapshot
from ..resources.base import to_wire_context
from ..resources.errors import stack_frames
from ..resources.remote_config import infer_value_type
from ..resources.voice import ENDPOINTS, PRESENCE, ROLES

if TYPE_CHECKING:  # pragma: no cover
    from .client import AsyncNexusClient

__all__ = [
    "AsyncResource",
    "AsyncSessions",
    "AsyncEvents",
    "AsyncLogs",
    "AsyncErrors",
    "AsyncFlags",
    "AsyncRemoteConfig",
    "AsyncLinks",
    "AsyncRealtime",
    "AsyncSurveys",
    "AsyncPush",
    "AsyncInApp",
    "AsyncLiveActivities",
    "AsyncReplay",
    "AsyncVoice",
]


class AsyncResource:
    """Base for the async resource namespaces."""

    def __init__(self, client: "AsyncNexusClient") -> None:
        self._client = client

    async def _send(self, request: p.Request) -> Any:
        return await self._client.request(request)

    def _context(self, **overrides: Any) -> Dict[str, Any]:
        return self._client.resolve_context(**to_wire_context(overrides))

    @staticmethod
    def _dict(value: Any) -> Dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _list(value: Any, key: Optional[str] = None) -> list:
        if isinstance(value, Mapping) and key is not None:
            value = value.get(key)
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _written(result: Any, fallback: int) -> int:
        if isinstance(result, Mapping):
            written = result.get("written")
            if isinstance(written, int):
                return written
        return fallback


# ── Sessions ─────────────────────────────────────────────────────────────────


class AsyncSessions(AsyncResource):
    """``client.sessions`` — see :class:`nexus.resources.Sessions`."""

    async def identify(
        self,
        distinct_id: str,
        *,
        email: Optional[str] = None,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        traits: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._dict(await self._send(p.sessions_identify(distinct_id, email, name, phone, traits)))

    async def track(
        self,
        *,
        distinct_id: Optional[str] = None,
        email: Optional[str] = None,
        name: Optional[str] = None,
        traits: Optional[Mapping[str, Any]] = None,
        device_key: Optional[str] = None,
        session_key: Optional[str] = None,
        browser: Optional[str] = None,
        app_version: Optional[str] = None,
        country: Optional[str] = None,
        entry_url: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        fields = self._context(
            distinct_id=distinct_id,
            device_key=device_key,
            session_key=session_key,
            browser=browser,
            app_version=app_version,
            country=country,
            os_type=os_type,
            os_version=os_version,
        )
        if email is not None:
            fields["email"] = email
        if name is not None:
            fields["name"] = name
        if traits:
            fields["traits"] = dict(traits)
        if entry_url is not None:
            fields["entryUrl"] = entry_url
        elif fields.get("url"):
            fields["entryUrl"] = fields["url"]
        return self._dict(await self._send(p.sessions_track(fields)))


# ── Events ───────────────────────────────────────────────────────────────────

_INGEST_KWARGS = (
    "distinct_id",
    "session_key",
    "device_key",
    "release",
    "app_version",
    "os_type",
    "os_version",
    "browser",
)


class AsyncEvents(AsyncResource):
    """``client.events`` — see :class:`nexus.resources.Events`."""

    def track(
        self,
        name: str,
        properties: Optional[Mapping[str, Any]] = None,
        *,
        timestamp: Optional[str] = None,
        **options: Any,
    ) -> None:
        """Buffer one event. Synchronous — buffering never awaits."""
        merged: Dict[str, Any] = dict(self._client.config.default_properties)
        ambient = self._client.ambient_properties()
        if ambient:
            merged.update(ambient)
        if properties:
            merged.update(properties)
        merged.update({k: v for k, v in options.items() if k not in _INGEST_KWARGS})

        item = p.event_item(name, merged or None, timestamp)
        context = self._context(**{k: options.get(k) for k in _INGEST_KWARGS})
        self._client.enqueue_event(item, context)

    capture = track

    async def batch(self, events: Iterable[Mapping[str, Any]], **options: Any) -> int:
        items: Sequence[Mapping[str, Any]] = [dict(e) for e in events]
        if not items:
            return 0
        context = self._context(**{k: options.get(k) for k in _INGEST_KWARGS})
        return self._written(await self._send(p.events_batch(items, context)), len(items))

    async def flush(self) -> None:
        await self._client.flush_events()


# ── Logs ─────────────────────────────────────────────────────────────────────


class AsyncLogs(AsyncResource):
    """``client.logs`` — see :class:`nexus.resources.Logs`."""

    def log(
        self,
        level: str,
        message: str,
        *,
        source: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[str] = None,
        **options: Any,
    ) -> None:
        """Buffer one log line. Synchronous — buffering never awaits."""
        merged: Dict[str, Any] = dict(context or {})
        merged.update({k: v for k, v in options.items() if k not in _INGEST_KWARGS})
        item = p.log_item(level, message, source, merged or None, timestamp)
        identity = self._context(**{k: options.get(k) for k in _INGEST_KWARGS})
        self._client.enqueue_log(item, identity)

    def trace(self, message: str, **options: Any) -> None:
        self.log("trace", message, **options)

    def debug(self, message: str, **options: Any) -> None:
        self.log("debug", message, **options)

    def info(self, message: str, **options: Any) -> None:
        self.log("info", message, **options)

    def warn(self, message: str, **options: Any) -> None:
        self.log("warn", message, **options)

    warning = warn

    def error(self, message: str, **options: Any) -> None:
        self.log("error", message, **options)

    def fatal(self, message: str, **options: Any) -> None:
        self.log("fatal", message, **options)

    critical = fatal

    async def batch(self, logs: Iterable[Mapping[str, Any]], **options: Any) -> int:
        items: Sequence[Mapping[str, Any]] = [dict(line) for line in logs]
        if not items:
            return 0
        identity = self._context(**{k: options.get(k) for k in _INGEST_KWARGS})
        return self._written(await self._send(p.logs_batch(items, identity)), len(items))

    async def flush(self) -> None:
        await self._client.flush_logs()


# ── Errors ───────────────────────────────────────────────────────────────────


class AsyncErrors(AsyncResource):
    """``client.errors`` — see :class:`nexus.resources.Errors`."""

    async def capture(
        self,
        message: str,
        *,
        type: Optional[str] = None,  # noqa: A002
        level: Optional[str] = None,
        handled: Optional[bool] = None,
        fingerprint: Optional[str] = None,
        stack: Optional[Union[str, Sequence[Mapping[str, Any]]]] = None,
        context: Optional[Mapping[str, Any]] = None,
        url: Optional[str] = None,
        **options: Any,
    ) -> Dict[str, Any]:
        merged_context: Dict[str, Any] = dict(context or {})
        merged_context.update({k: v for k, v in options.items() if k not in _INGEST_KWARGS})
        fields = self._context(url=url, **{k: options.get(k) for k in _INGEST_KWARGS})
        fields.update(
            {
                "message": message,
                "type": type,
                "level": level,
                "handled": handled,
                "fingerprint": fingerprint,
                "stack": list(stack) if isinstance(stack, (list, tuple)) else stack,
                "context": merged_context or None,
            }
        )
        return self._dict(await self._send(p.errors_capture(fields)))

    async def capture_exception(
        self,
        exc: Optional[BaseException] = None,
        *,
        handled: bool = True,
        level: Optional[str] = "error",
        **options: Any,
    ) -> Dict[str, Any]:
        if exc is None:
            import sys

            exc = sys.exc_info()[1]
        if exc is None:
            return {}
        options.setdefault("type", type(exc).__name__)
        return await self.capture(
            str(exc) or type(exc).__name__,
            level=level,
            handled=handled,
            stack=stack_frames(exc) or None,
            **options
        )


# ── Feature flags ────────────────────────────────────────────────────────────


class AsyncFlags(AsyncResource):
    """``client.flags`` — see :class:`nexus.resources.Flags`."""

    async def evaluate(
        self,
        distinct_id: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        **extra_properties: Any,
    ) -> FlagEvaluation:
        person = distinct_id or self._client.current_distinct_id()
        if not person:
            raise ValueError(
                "flags.evaluate() needs a distinct_id (pass one, or bind it with nexus.context)."
            )
        merged = dict(properties or {})
        merged.update(extra_properties)
        return FlagEvaluation.from_response(await self._send(p.flags_evaluate(person, merged or None)))

    async def all(self, distinct_id: Optional[str] = None, properties: Optional[Mapping[str, Any]] = None, **kw: Any):
        return (await self.evaluate(distinct_id, properties, **kw)).flags

    async def _safe_evaluate(self, distinct_id, properties, extra) -> Optional[FlagEvaluation]:
        """Evaluate without letting a flag outage break the caller — see
        :meth:`nexus.resources.Flags._safe_evaluate`."""
        try:
            return await self.evaluate(distinct_id, properties, **dict(extra))
        except NexusError as exc:
            logger.warning("nexus: flag evaluation failed, using the default: %s", exc)
            return None

    async def is_enabled(
        self,
        key: str,
        distinct_id: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        *,
        default: bool = False,
        **extra_properties: Any,
    ) -> bool:
        result = await self._safe_evaluate(distinct_id, properties, extra_properties)
        if result is None or not result.flags:
            return default
        return result.is_enabled(key)

    async def variant(
        self, key: str, distinct_id: Optional[str] = None, properties=None, *, default=None, **kw: Any
    ):
        result = await self._safe_evaluate(distinct_id, properties, kw)
        return default if result is None else (result.variant(key) or default)

    async def payload(
        self, key: str, distinct_id: Optional[str] = None, properties=None, *, default: Any = None, **kw: Any
    ):
        result = await self._safe_evaluate(distinct_id, properties, kw)
        return default if result is None else result.payload(key, default)


# ── Remote Config ────────────────────────────────────────────────────────────


class AsyncRemoteConfig(AsyncResource):
    """``client.remote_config`` — see :class:`nexus.resources.RemoteConfig`."""

    def __init__(self, client: "AsyncNexusClient", *, cache_ttl: float = 30.0) -> None:
        super().__init__(client)
        self.cache_ttl = cache_ttl
        self._defaults: Dict[str, Any] = {}
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def set_defaults(self, defaults: Mapping[str, Any]) -> None:
        self._defaults.update(defaults)

    @property
    def defaults(self) -> Mapping[str, Any]:
        return dict(self._defaults)

    async def fetch(
        self,
        context: Optional[Mapping[str, Any]] = None,
        *,
        etag: Optional[str] = None,
        fresh: bool = False,
        **overrides: Any,
    ) -> RemoteConfigSnapshot:
        resolved = self._resolve_context(context, **overrides)
        key = repr(sorted(resolved.items()))
        with self._lock:
            entry = self._cache.get(key)
        if entry and not fresh and etag is None and (time.monotonic() - entry["at"]) < self.cache_ttl:
            return entry["snapshot"]

        previous: Optional[RemoteConfigSnapshot] = entry["snapshot"] if entry else None
        conditional = etag if etag is not None else (previous.etag if previous else None)
        payload = await self._send(p.remote_config_fetch(resolved, conditional or None))
        if payload is None and previous is not None:
            return previous
        snapshot = RemoteConfigSnapshot.from_response(payload, defaults=self._defaults, previous=previous)
        with self._lock:
            self._cache[key] = {"snapshot": snapshot, "at": time.monotonic()}
        return snapshot

    async def all(self, context: Optional[Mapping[str, Any]] = None, **o: Any) -> Dict[str, Any]:
        return (await self.fetch(context, **o)).to_dict()

    async def get(self, key: str, default: Any = None, context: Optional[Mapping[str, Any]] = None, **o: Any) -> Any:
        return (await self.fetch(context, **o)).get(key, default)

    async def get_string(self, key: str, default: str = "", context=None, **o: Any) -> str:
        return (await self.fetch(context, **o)).get_string(key, default)

    async def get_bool(self, key: str, default: bool = False, context=None, **o: Any) -> bool:
        return (await self.fetch(context, **o)).get_bool(key, default)

    async def get_number(self, key: str, default: float = 0.0, context=None, **o: Any) -> float:
        return (await self.fetch(context, **o)).get_number(key, default)

    async def get_int(self, key: str, default: int = 0, context=None, **o: Any) -> int:
        return (await self.fetch(context, **o)).get_int(key, default)

    async def get_json(self, key: str, default: Any = None, context=None, **o: Any) -> Any:
        return (await self.fetch(context, **o)).get_json(key, default)

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    async def set_parameter(
        self,
        key: str,
        value: Any = None,
        *,
        value_type: Optional[str] = None,
        conditional_values: Optional[Mapping[str, Any]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_type = (value_type or infer_value_type(value)).upper()
        return self._dict(
            await self._send(
                p.remote_config_set_parameter(key, resolved_type, value, conditional_values, description)
            )
        )

    async def delete_parameter(self, key: str) -> None:
        await self._send(p.remote_config_delete_parameter(key))

    async def publish(self, description: Optional[str] = None) -> Dict[str, Any]:
        result = self._dict(await self._send(p.remote_config_publish(description)))
        self.invalidate()
        return result

    async def set(self, key: str, value: Any, *, value_type: Optional[str] = None, description=None):
        await self.set_parameter(key, value, value_type=value_type, description=description)
        return await self.publish(description)

    def _resolve_context(self, context: Optional[Mapping[str, Any]], **overrides: Any) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        if context:
            merged.update(to_wire_context(context))
        merged.update({k: v for k, v in to_wire_context(overrides).items() if v is not None})
        config = self._client.config
        merged.setdefault("platform", "server")
        if config.app_version:
            merged.setdefault("appVersion", config.app_version)
        if config.os_version:
            merged.setdefault("osVersion", config.os_version)
        ambient = self._client.resolve_context()
        for wire_key in ("appVersion", "osVersion", "country"):
            if ambient.get(wire_key):
                merged.setdefault(wire_key, ambient[wire_key])
        if merged.get("userProperties"):
            merged["userProperties"] = dict(merged["userProperties"])
        return {k: v for k, v in merged.items() if v is not None}


# ── Links ────────────────────────────────────────────────────────────────────


class AsyncLinks(AsyncResource):
    """``client.links`` — see :class:`nexus.resources.Links`."""

    async def attribute(
        self,
        type: str = "install",  # noqa: A002
        *,
        name: Optional[str] = None,
        click_id: Optional[str] = None,
        distinct_id: Optional[str] = None,
        device_id: Optional[str] = None,
        session_key: Optional[str] = None,
        platform: Optional[str] = None,
        os_type: Optional[str] = None,
        country: Optional[str] = None,
        revenue: Optional[float] = None,
        properties: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        fields = self._context(
            distinct_id=distinct_id, session_key=session_key, os_type=os_type, country=country
        )
        fields.update(
            {
                "type": type,
                "name": name,
                "clickId": click_id,
                "deviceId": device_id,
                "platform": platform,
                "revenue": revenue,
                "properties": dict(properties) if properties else None,
            }
        )
        return self._dict(await self._send(p.links_attribute(fields)))

    async def install(self, **o: Any) -> Dict[str, Any]:
        return await self.attribute("install", **o)

    async def open(self, **o: Any) -> Dict[str, Any]:
        return await self.attribute("open", **o)

    async def reengagement(self, **o: Any) -> Dict[str, Any]:
        return await self.attribute("reengagement", **o)


# ── Realtime ─────────────────────────────────────────────────────────────────


class AsyncRealtime(AsyncResource):
    """``client.realtime`` — see :class:`nexus.resources.Realtime`."""

    async def emit(
        self, room: Union[str, RoomMessage], events: Union[str, Sequence[str]] = (), payload: Any = None
    ) -> Dict[str, Any]:
        message = room if isinstance(room, RoomMessage) else RoomMessage(room, events, payload)
        return self._dict(await self._send(p.realtime_emit(message)))

    async def broadcast(self, messages: Iterable[Union[RoomMessage, Mapping[str, Any]]]) -> Dict[str, Any]:
        items = list(messages)
        if not items:
            return {}
        return self._dict(await self._send(p.realtime_broadcast(items)))

    async def emit_to_rooms(self, rooms: Sequence[str], events, payload: Any = None) -> Dict[str, Any]:
        return await self.broadcast([RoomMessage(room, events, payload) for room in rooms])

    async def register_room(self, name: str, type: Optional[str] = None) -> Dict[str, Any]:  # noqa: A002
        return self._dict(await self._send(p.realtime_register_room(name, type)))

    async def rooms(self) -> List[Any]:
        return self._list(await self._send(p.realtime_list_rooms()), "rooms")

    async def delete_room(self, room_id: str) -> None:
        await self._send(p.realtime_delete_room(room_id))

    async def link(self, room_id: str, related_id: str) -> None:
        await self._send(p.realtime_link(room_id, related_id))

    async def unlink(self, room_id: str, related_id: str) -> None:
        await self._send(p.realtime_unlink(room_id, related_id))

    async def related(self, name: str) -> Any:
        return await self._send(p.realtime_related(name))

    async def schema(self, room_id: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.realtime_get_schema(room_id)))

    async def set_schema(self, room_id: str, schema: Mapping[str, Any]) -> Dict[str, Any]:
        return self._dict(await self._send(p.realtime_set_schema(room_id, schema)))

    async def enable_schema(self, room_id: str, enabled: bool = True) -> Dict[str, Any]:
        return self._dict(await self._send(p.realtime_enable_schema(room_id, enabled)))

    async def clear_schema(self, room_id: str) -> None:
        await self._send(p.realtime_clear_schema(room_id))


# ── Surveys ──────────────────────────────────────────────────────────────────


class AsyncSurveys(AsyncResource):
    """``client.surveys`` — see :class:`nexus.resources.Surveys`."""

    async def active(
        self,
        *,
        distinct_id: Optional[str] = None,
        device_key: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        device_type: Optional[str] = None,
        os_type: Optional[str] = None,
    ) -> List[Any]:
        fields = self._context(distinct_id=distinct_id, device_key=device_key, os_type=os_type)
        fields["properties"] = dict(properties) if properties else None
        fields["deviceType"] = device_type
        return self._list(await self._send(p.surveys_active(fields)), "surveys")

    async def respond(
        self,
        survey_id: str,
        answers: Optional[Mapping[str, Any]] = None,
        *,
        completed: Optional[bool] = None,
        dismissed: Optional[bool] = None,
        response_id: Optional[str] = None,
        iteration_key: Optional[str] = None,
        **options: Any,
    ) -> Dict[str, Any]:
        fields = self._context(**options)
        fields.update(
            {
                "completed": completed,
                "dismissed": dismissed,
                "responseId": response_id,
                "iterationKey": iteration_key,
            }
        )
        return self._dict(await self._send(p.surveys_respond(survey_id, answers, fields)))

    async def complete(self, survey_id: str, answers: Mapping[str, Any], **o: Any) -> Dict[str, Any]:
        o["completed"] = True
        return await self.respond(survey_id, answers, **o)

    async def dismiss(self, survey_id: str, **o: Any) -> Dict[str, Any]:
        o["dismissed"] = True
        return await self.respond(survey_id, None, **o)


# ── Push ─────────────────────────────────────────────────────────────────────


class AsyncPush(AsyncResource):
    """``client.push`` — see :class:`nexus.resources.Push`.

    The builder is shared with the sync SDK; ``send()`` returns an awaitable
    here, so ``await client.push.notification()...send()``.
    """

    def notification(self) -> PushBuilder:
        return PushBuilder(self.send_raw)  # type: ignore[arg-type]

    async def send(
        self,
        distinct_ids: Sequence[str],
        message: Optional[Mapping[str, Any]] = None,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        image_url: Optional[str] = None,
        data: Optional[Mapping[str, Any]] = None,
        options: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = dict(message or {})
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if image_url is not None:
            payload["imageUrl"] = image_url
        if data is not None:
            payload["data"] = dict(data)
        if options is not None:
            payload["options"] = dict(options)
        if not payload.get("title"):
            raise ValueError("push.send() requires a title.")
        payload["distinctIds"] = list(distinct_ids)
        return await self.send_raw(payload)

    async def send_to_user(self, distinct_id: str, message=None, **kw: Any) -> Dict[str, Any]:
        return await self.send([distinct_id], message, **kw)

    async def send_raw(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        return self._dict(await self._send(p.push_send(payload)))

    async def register_token(
        self,
        token: str,
        platform: str,
        *,
        provider: Optional[str] = None,
        lang: Optional[str] = None,
        tags: Optional[Mapping[str, str]] = None,
        **options: Any,
    ) -> Dict[str, Any]:
        fields = self._context(**options)
        fields.update(
            {
                "token": token,
                "platform": platform,
                "provider": provider or ("webpush" if platform == "web" else "fcm"),
                "lang": lang,
                "tags": dict(tags) if tags else None,
            }
        )
        return self._dict(await self._send(p.push_register(fields)))

    async def unregister(self, token: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.push_unregister(token)))

    async def opened(self, notification_id: str, **options: Any) -> Dict[str, Any]:
        fields = self._context(**options)
        fields["notificationId"] = notification_id
        return self._dict(await self._send(p.push_opened(fields)))


# ── In-app messages ──────────────────────────────────────────────────────────


class AsyncInApp(AsyncResource):
    """``client.inapp`` — see :class:`nexus.resources.InApp`."""

    async def active(self, *, distinct_id: Optional[str] = None, device_key: Optional[str] = None) -> List[Any]:
        ctx = self._context(distinct_id=distinct_id, device_key=device_key)
        return self._list(
            await self._send(p.inapp_active(ctx.get("distinctId"), ctx.get("deviceKey"))), "messages"
        )

    async def track_event(
        self,
        message_id: str,
        type: str,  # noqa: A002
        *,
        button_id: Optional[str] = None,
        distinct_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        ctx = self._context(distinct_id=distinct_id)
        return self._dict(await self._send(p.inapp_event(message_id, type, button_id, ctx.get("distinctId"))))

    async def impression(self, message_id: str, **o: Any) -> Dict[str, Any]:
        return await self.track_event(message_id, "impression", **o)

    async def click(self, message_id: str, button_id: str, **o: Any) -> Dict[str, Any]:
        return await self.track_event(message_id, "click", button_id=button_id, **o)

    async def dismiss(self, message_id: str, **o: Any) -> Dict[str, Any]:
        return await self.track_event(message_id, "dismiss", **o)


# ── Live Activities ──────────────────────────────────────────────────────────


class AsyncLiveActivities(AsyncResource):
    """``client.live_activities`` — see :class:`nexus.resources.LiveActivities`.

    The builder's ``start()`` / ``update()`` / ``end()`` return awaitables here.
    """

    def activity(self, activity_type_or_id: str, activity_id: Optional[str] = None) -> LiveActivityBuilder:
        if activity_id is None:
            return LiveActivityBuilder(self.start, self.update, self.end, activity_type_or_id)  # type: ignore[arg-type]
        return LiveActivityBuilder(
            self.start, self.update, self.end, activity_id, activity_type_or_id  # type: ignore[arg-type]
        )

    async def start(
        self,
        activity_type: str,
        activity_id: str,
        content_state: Optional[Mapping[str, Any]] = None,
        *,
        distinct_ids: Optional[Sequence[str]] = None,
        attributes: Optional[Mapping[str, Any]] = None,
        stale_date: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._dict(
            await self._send(
                p.live_activity_start(
                    activity_type, activity_id, content_state, distinct_ids, attributes, stale_date, priority
                )
            )
        )

    async def update(
        self,
        activity_id: str,
        content_state: Optional[Mapping[str, Any]] = None,
        *,
        stale_date: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._dict(
            await self._send(p.live_activity_update(activity_id, content_state, stale_date, priority))
        )

    async def end(
        self,
        activity_id: str,
        content_state: Optional[Mapping[str, Any]] = None,
        *,
        dismissal_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._dict(await self._send(p.live_activity_end(activity_id, content_state, dismissal_date)))

    async def register_push_to_start_token(
        self, activity_type: str, token: str, *, distinct_id=None, device_key=None
    ) -> Dict[str, Any]:
        ctx = self._context(distinct_id=distinct_id, device_key=device_key)
        return self._dict(
            await self._send(
                p.live_activity_push_token(activity_type, token, ctx.get("distinctId"), ctx.get("deviceKey"))
            )
        )

    async def register_activity(
        self,
        activity_id: str,
        activity_type: str,
        *,
        platform: str = "ios",
        update_token: Optional[str] = None,
        distinct_id: Optional[str] = None,
        content_state: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = self._context(distinct_id=distinct_id)
        return self._dict(
            await self._send(
                p.live_activity_register(
                    activity_id, activity_type, platform, update_token, ctx.get("distinctId"), content_state
                )
            )
        )

    async def event(self, activity_id: str, type: str, *, platform: Optional[str] = None):  # noqa: A002
        return self._dict(await self._send(p.live_activity_event(activity_id, type, platform)))


# ── Replay ───────────────────────────────────────────────────────────────────


class AsyncReplay(AsyncResource):
    """``client.replay`` — see :class:`nexus.resources.Replay`."""

    async def ingest(
        self,
        recording_id: str,
        events: Sequence[Mapping[str, Any]],
        *,
        href: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        **options: Any,
    ) -> Dict[str, Any]:
        items = [dict(e) for e in events]
        if not items:
            return {}
        if len(items) > 5000:
            raise ValueError("replay.ingest() accepts at most 5000 events per chunk.")
        fields = self._context(url=href, **options)
        fields["recordingId"] = recording_id
        fields["href"] = href or fields.get("url")
        fields["width"] = width
        fields["height"] = height
        return self._dict(await self._send(p.replay_ingest(items, fields)))


# ── Voice ────────────────────────────────────────────────────────────────────


class AsyncVoice(AsyncResource):
    """``client.voice`` — see :class:`nexus.resources.Voice`."""

    async def create_call(
        self,
        *,
        direction: Optional[str] = None,
        application_id: Optional[str] = None,
        proxy_identity_id: Optional[str] = None,
        recording_mode: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._dict(
            await self._send(
                p.voice_create_call(direction, application_id, proxy_identity_id, recording_mode, metadata)
            )
        )

    async def add_leg(
        self,
        session_id: str,
        role: str,
        endpoint_type: str,
        *,
        direction: Optional[str] = None,
        address: Optional[str] = None,
        caller_id: Optional[str] = None,
        identity_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if role not in ROLES:
            raise ValueError("role must be one of {0}".format(", ".join(ROLES)))
        if endpoint_type not in ENDPOINTS:
            raise ValueError("endpoint_type must be one of {0}".format(", ".join(ENDPOINTS)))
        return self._dict(
            await self._send(
                p.voice_add_leg(
                    {
                        "sessionId": session_id,
                        "role": role,
                        "endpointType": endpoint_type,
                        "direction": direction,
                        "address": address,
                        "callerId": caller_id,
                        "identityId": identity_id,
                    }
                )
            )
        )

    async def ring(self, leg_id: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_leg_action("ring", leg_id)))

    async def answer(self, leg_id: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_leg_action("answer", leg_id)))

    async def hold(self, leg_id: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_leg_action("hold", leg_id)))

    async def resume(self, leg_id: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_leg_action("resume", leg_id)))

    async def hangup(self, leg_id: str, reason: str = "hangup") -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_leg_action("hangup", leg_id, reason)))

    async def dtmf(self, session_id: str, leg_id: str, digit: str, *, duration_ms: Optional[int] = None):
        return self._dict(await self._send(p.voice_dtmf(session_id, leg_id, digit, duration_ms)))

    async def ivr_start(self, flow_id: str, *, application_id: Optional[str] = None) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_ivr_start(flow_id, application_id)))

    async def ivr_advance(self, session_id: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_ivr_advance(session_id)))

    async def ivr_input(self, session_id: str, digit: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_ivr_input(session_id, digit)))

    async def register_device(
        self,
        identity_id: str,
        device_id: str,
        platform: str,
        *,
        voip_token: Optional[str] = None,
        fcm_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._dict(
            await self._send(p.voice_register_device(identity_id, device_id, platform, voip_token, fcm_token))
        )

    async def presence(self, identity_id: str, device_id: str, status: str) -> Dict[str, Any]:
        if status not in PRESENCE:
            raise ValueError("status must be one of {0}".format(", ".join(PRESENCE)))
        return self._dict(await self._send(p.voice_presence(identity_id, device_id, status)))

    async def report_quality(
        self,
        session_id: str,
        *,
        leg_id: Optional[str] = None,
        rtt_ms: Optional[int] = None,
        jitter_ms: Optional[int] = None,
        packet_loss: Optional[float] = None,
        bitrate_kbps: Optional[int] = None,
        mos: Optional[float] = None,
        codec: Optional[str] = None,
        candidate_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._dict(
            await self._send(
                p.voice_quality(
                    {
                        "sessionId": session_id,
                        "legId": leg_id,
                        "rttMs": rtt_ms,
                        "jitterMs": jitter_ms,
                        "packetLoss": packet_loss,
                        "bitrateKbps": bitrate_kbps,
                        "mos": mos,
                        "codec": codec,
                        "candidateType": candidate_type,
                    }
                )
            )
        )

    async def transfer_blind(
        self,
        session_id: str,
        leg_id: str,
        target_type: str,
        *,
        target_address: Optional[str] = None,
        target_identity_id: Optional[str] = None,
        caller_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._dict(
            await self._send(
                p.voice_transfer_blind(
                    session_id, leg_id, target_type, target_address, target_identity_id, caller_id
                )
            )
        )

    async def transfer_warm_start(
        self,
        session_id: str,
        other_party_leg_id: str,
        target_type: str,
        *,
        target_address: Optional[str] = None,
        target_identity_id: Optional[str] = None,
        caller_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._dict(
            await self._send(
                p.voice_transfer_warm_start(
                    session_id, other_party_leg_id, target_type, target_address, target_identity_id, caller_id
                )
            )
        )

    async def transfer_warm_complete(self, other_party_leg_id: str, leg_id: str) -> Dict[str, Any]:
        return self._dict(
            await self._send(p.voice_transfer_warm_finish("complete", other_party_leg_id, leg_id))
        )

    async def transfer_warm_cancel(self, other_party_leg_id: str, consult_leg_id: str) -> Dict[str, Any]:
        return self._dict(
            await self._send(p.voice_transfer_warm_finish("cancel", other_party_leg_id, consult_leg_id))
        )

    async def mute(self, leg_id: str, muted: bool = True) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_conference_mute(leg_id, muted)))

    async def remove(self, session_id: str, leg_id: str) -> Dict[str, Any]:
        return self._dict(await self._send(p.voice_conference_remove(session_id, leg_id)))

    def recording_download_url(self, access_token: str, *, inline: bool = False) -> str:
        from urllib.parse import urlencode

        query = {"token": access_token}
        if inline:
            query["inline"] = "1"
        return "{0}/partner/voice/recordings/download?{1}".format(
            self._client.config.http_base, urlencode(query)
        )
