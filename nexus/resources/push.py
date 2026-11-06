"""Push notifications — transactional sends to users, segments or filters.

Campaigns, provider credentials, segments and templates live in the console;
this resource is for one-off, event-driven sends from your backend. Delivered
over FCM / APNs / Web Push.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Union

from .. import _payloads as p
from ..builders.push import PushBuilder, _expand_translations
from .base import Resource

__all__ = ["Push"]


class Push(Resource):
    """``client.push``"""

    def notification(self) -> PushBuilder:
        """Start a fluent notification — the recommended way to send."""
        return PushBuilder(self.send_raw)

    def send(
        self,
        distinct_ids: Sequence[str],
        message: Optional[Mapping[str, Any]] = None,
        *,
        title: Union[str, Mapping[str, Optional[str]], None] = None,
        body: Union[str, Mapping[str, Optional[str]], None] = None,
        image_url: Optional[str] = None,
        data: Optional[Mapping[str, Any]] = None,
        options: Optional[Mapping[str, Any]] = None,
        default_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send to specific users across every device each has registered.

        ::

            client.push.send(["user_1", "user_2"], title="Welcome 👋")
            client.push.send(["user_1"], {"title": "Hi", "data": {"screen": "/home"}})
        """
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
        if default_language is not None:
            payload["defaultLanguage"] = default_language
        if not payload.get("title"):
            raise ValueError("push.send() requires a title.")
        payload = _expand_translations(payload)
        if not payload.get("title"):
            raise ValueError("push.send() requires a title.")
        payload["distinctIds"] = list(distinct_ids)
        return self.send_raw(payload)

    def send_to_user(self, distinct_id: str, message: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        """Send to one user."""
        return self.send([distinct_id], message, **kwargs)

    def send_to_segments(
        self, segment_ids: Iterable[str], message: Optional[Mapping[str, Any]] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        """Send to the union of saved segments."""
        builder = self.notification()
        payload = dict(message or {})
        for key, value in kwargs.items():
            payload.setdefault(key, value)
        if payload.get("title"):
            builder.title(payload["title"])  # a string or a per-language map
        if payload.get("body"):
            builder.body(payload["body"])
        builder.default_language(payload.get("default_language") or payload.get("defaultLanguage"))
        if payload.get("image_url") or payload.get("imageUrl"):
            builder.image(str(payload.get("image_url") or payload.get("imageUrl")))
        if payload.get("data"):
            builder.data(payload["data"])
        if payload.get("options"):
            builder.options(payload["options"])
        return builder.to_segments(segment_ids).send()

    def schedule(
        self,
        distinct_ids: Sequence[str],
        send_at: Any,
        message: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Deliver to specific users later. ``send_at`` is an aware ``datetime``,
        epoch seconds or an ISO-8601 string with an offset.

        Returns the scheduled send ``{id, status, scheduledAt, …}`` — keep the
        ``id`` to check on it (:meth:`scheduled`) or cancel it (:meth:`cancel_scheduled`).
        """
        payload = dict(message or {})
        payload["sendAt"] = p.send_at_value(send_at)
        return self.send(distinct_ids, payload, **kwargs)

    def send_raw(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Send a fully assembled payload (what the builder calls).

        Returns ``{sent, failed, recipients}``, or the scheduled send when the
        payload carries ``sendAt``.
        """
        return self._dict(self._send(p.push_send(payload)))

    def scheduled(self, scheduled_id: str) -> Dict[str, Any]:
        """A scheduled send's status — and ``sent``/``failed``/``recipients`` once it fired."""
        return self._dict(self._send(p.push_scheduled(scheduled_id)))

    def cancel_scheduled(self, scheduled_id: str) -> Dict[str, Any]:
        """Cancel a scheduled send before it fires. The API answers 409 once it
        has started sending, 404 for an unknown id."""
        return self._dict(self._send(p.push_cancel_scheduled(scheduled_id)))

    # ── device tokens ────────────────────────────────────────────────────────

    def register_token(
        self,
        token: str,
        platform: str,
        *,
        provider: Optional[str] = None,
        distinct_id: Optional[str] = None,
        device_key: Optional[str] = None,
        app_version: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        lang: Optional[str] = None,
        tags: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        """Register or refresh a device token.

        The client SDKs normally do this; it's here for server-driven and
        bring-your-own-token flows. ``platform`` is ios | android | web.
        """
        fields = self._context(
            distinct_id=distinct_id,
            device_key=device_key,
            app_version=app_version,
            os_type=os_type,
            os_version=os_version,
        )
        fields.update(
            {
                "token": token,
                "platform": platform,
                "provider": provider or ("webpush" if platform == "web" else "fcm"),
                "lang": lang,
                "tags": dict(tags) if tags else None,
            }
        )
        return self._dict(self._send(p.push_register(fields)))

    def unregister(self, token: str) -> Dict[str, Any]:
        """Stop delivering to a token (logout, uninstall)."""
        return self._dict(self._send(p.push_unregister(token)))

    def opened(self, notification_id: str, **options: Any) -> Dict[str, Any]:
        """Record that a notification was opened (delivery analytics)."""
        fields = self._context(**options)
        fields["notificationId"] = notification_id
        return self._dict(self._send(p.push_opened(fields)))
