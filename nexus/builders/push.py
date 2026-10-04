"""Fluent builder for a transactional push notification."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .._payloads import compact

__all__ = ["PushBuilder"]


class PushBuilder:
    """Compose content, per-platform options and an audience, then ``send()``.

    ::

        (client.push.notification()
            .title("Your order shipped")
            .body("Track it in the app")
            .image("https://cdn.example.com/box.png")
            .data(screen="/orders/42")
            .button("track", "Track", url="https://x/track")
            .ios_badge(1)
            .android_visibility("public")
            .to_segments(["vip", "active_7d"])
            .send())          # → {"sent": .., "failed": .., "recipients": ..}

    Targeting combines as a union: :meth:`to_users`, :meth:`to_segments`,
    :meth:`where`, or :meth:`to_all` (which overrides the rest).
    """

    def __init__(self, sender: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        self._sender = sender
        self._title: Optional[str] = None
        self._body: Optional[str] = None
        self._image_url: Optional[str] = None
        self._data: Dict[str, Any] = {}
        self._options: Dict[str, Any] = {}
        self._distinct_ids: List[str] = []
        self._segment_ids: List[str] = []
        self._filter: Dict[str, Any] = {}
        self._all = False

    # ── content ──────────────────────────────────────────────────────────────

    def title(self, title: str) -> "PushBuilder":
        self._title = title
        return self

    def body(self, body: str) -> "PushBuilder":
        self._body = body
        return self

    def image(self, url: str) -> "PushBuilder":
        self._image_url = url
        return self

    def data(self, data: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> "PushBuilder":
        """Custom key/values delivered to the app (merged with any prior data)."""
        if data:
            self._data.update(data)
        self._data.update(kwargs)
        return self

    # ── options ──────────────────────────────────────────────────────────────

    def button(
        self,
        id: str,  # noqa: A002 - mirrors the API field name
        text: str,
        *,
        url: Optional[str] = None,
        event: Optional[str] = None,
        icon: Optional[str] = None,
        style: Optional[str] = None,
    ) -> "PushBuilder":
        """Add an action button. ``style`` is primary | secondary | text."""
        buttons: List[Dict[str, Any]] = self._options.setdefault("buttons", [])
        buttons.append(compact({"id": id, "text": text, "url": url, "event": event, "icon": icon, "style": style}))
        return self

    def ios_badge(self, count: int) -> "PushBuilder":
        return self.option("iosBadge", int(count))

    def ios_relevance_score(self, score: float) -> "PushBuilder":
        return self.option("iosRelevanceScore", float(score))

    def ios_interruption_level(self, level: str) -> "PushBuilder":
        """passive | active | time-sensitive | critical."""
        return self.option("iosInterruptionLevel", level)

    def ios_subtitle(self, subtitle: str) -> "PushBuilder":
        return self.option("iosSubtitle", subtitle)

    def android_visibility(self, visibility: str) -> "PushBuilder":
        """Lock-screen visibility: public | private | secret."""
        return self.option("androidVisibility", visibility)

    def android_large_icon(self, url: str) -> "PushBuilder":
        return self.option("androidLargeIcon", url)

    def android_big_picture(self, url: str) -> "PushBuilder":
        return self.option("androidBigPicture", url)

    def android_accent_color(self, color: str) -> "PushBuilder":
        return self.option("androidAccentColor", color)

    def option(self, key: str, value: Any) -> "PushBuilder":
        """Set any option field directly (escape hatch for new server options)."""
        self._options[key] = value
        return self

    def options(self, options: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> "PushBuilder":
        if options:
            self._options.update(options)
        self._options.update(kwargs)
        return self

    # ── targeting ────────────────────────────────────────────────────────────

    def to_users(self, distinct_ids: Iterable[str]) -> "PushBuilder":
        for distinct_id in distinct_ids:
            if distinct_id not in self._distinct_ids:
                self._distinct_ids.append(distinct_id)
        return self

    def to_user(self, distinct_id: str) -> "PushBuilder":
        return self.to_users([distinct_id])

    def to_segments(self, segment_ids: Iterable[str]) -> "PushBuilder":
        for segment_id in segment_ids:
            if segment_id not in self._segment_ids:
                self._segment_ids.append(segment_id)
        return self

    #: Alias matching the PHP/JS SDKs.
    set_segments = to_segments

    def to_segment(self, segment_id: str) -> "PushBuilder":
        return self.to_segments([segment_id])

    def where(self, filter: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> "PushBuilder":  # noqa: A002
        """Ad-hoc device filter: platform, lang, osType, appVersion,
        lastSeenDays, tags."""
        if filter:
            self._filter.update(filter)
        self._filter.update(kwargs)
        return self

    def to_all(self) -> "PushBuilder":
        """Every subscribed device — overrides the other targets. Use with care."""
        self._all = True
        return self

    # ── terminal ─────────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """The assembled payload, without sending. Handy in tests."""
        return compact(
            {
                "title": self._title,
                "body": self._body,
                "imageUrl": self._image_url,
                "data": self._data or None,
                "options": self._options or None,
                "distinctIds": self._distinct_ids or None,
                "segmentIds": self._segment_ids or None,
                "filter": self._filter or None,
                "all": True if self._all else None,
            }
        )

    def _validate(self) -> Dict[str, Any]:
        if not self._title:
            raise ValueError("A push notification requires a title().")
        if not (self._all or self._distinct_ids or self._segment_ids or self._filter):
            raise ValueError(
                "A push notification requires a target: to_users(), to_segments(), where(), or to_all()."
            )
        return self.to_dict()

    def send(self) -> Dict[str, Any]:
        """Deliver it. Returns ``{sent, failed, recipients}``."""
        return self._sender(self._validate())
