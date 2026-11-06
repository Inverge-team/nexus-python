"""Fluent builder for a transactional push notification."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Union

from .._payloads import compact, send_at_value

__all__ = ["PushBuilder"]


def _translations(texts: Mapping[str, Optional[str]]) -> Dict[str, str]:
    """Keep the non-empty translations, keyed by stripped language code."""
    return {
        str(lang).strip(): text
        for lang, text in texts.items()
        if str(lang).strip() and isinstance(text, str) and text.strip()
    }


def _accepts_value(callback: Callable[..., Any]) -> bool:
    """Whether a ``when()`` callback takes ``(builder, value)`` or just ``(builder)``."""
    try:
        params = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):  # some builtins have no signature
        return True
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(positional) >= 2 or any(p.kind == p.VAR_POSITIONAL for p in params)


def _apply(callback: Callable[..., Any], builder: "PushBuilder", value: Any) -> None:
    if _accepts_value(callback):
        callback(builder, value)
    else:
        callback(builder)


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

    Branch without leaving the chain with :meth:`when` / :meth:`unless`
    (Laravel-style), and deliver later with :meth:`send_at`::

        (client.push.notification()
            .title(title)
            .when(image, lambda p, url: p.image(url))
            .when(not ids, lambda p: p.to_segment("All Clients"), lambda p: p.to_users(ids))
            .send_at(datetime(2026, 11, 7, 9, tzinfo=timezone.utc))
            .send())          # → {"id": .., "status": "scheduled", "scheduledAt": ..}
    """

    def __init__(self, sender: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        self._sender = sender
        self._title: Optional[str] = None
        self._body: Optional[str] = None
        self._titles: Dict[str, str] = {}
        self._bodies: Dict[str, str] = {}
        self._default_language: Optional[str] = None
        self._image_url: Optional[str] = None
        self._data: Dict[str, Any] = {}
        self._options: Dict[str, Any] = {}
        self._distinct_ids: List[str] = []
        self._segment_ids: List[str] = []
        self._filter: Dict[str, Any] = {}
        self._all = False
        self._send_at: Optional[str] = None
        self._name: Optional[str] = None

    # ── content ──────────────────────────────────────────────────────────────

    def title(self, title: Union[str, Mapping[str, Optional[str]]]) -> "PushBuilder":
        """The title — a string, or one per language:
        ``{"en": "Your order shipped", "ar": "تم شحن طلبك"}``. Each device gets its
        language's; an empty or missing translation falls back to the default
        language (see :meth:`default_language`).
        """
        if isinstance(title, Mapping):
            self._titles, self._title = _translations(title), None
        else:
            self._title, self._titles = title, {}
        return self

    def body(self, body: Union[str, Mapping[str, Optional[str]], None]) -> "PushBuilder":
        """The body — a string or one per language. ``None`` / ``""`` leaves it unset."""
        if isinstance(body, Mapping):
            self._bodies, self._body = _translations(body), None
        else:
            self._body, self._bodies = body or None, {}
        return self

    def default_language(self, lang: Optional[str]) -> "PushBuilder":
        """The language a device gets when its own has no translation. Defaults to
        ``en`` when there is one, else the first language given."""
        self._default_language = (lang or "").strip() or None
        return self

    def image(self, url: Optional[str]) -> "PushBuilder":
        """``None`` / ``""`` leaves the image unset."""
        self._image_url = url or None
        return self

    def data(self, data: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> "PushBuilder":
        """Custom key/values delivered to the app (merged with any prior data)."""
        if data:
            self._data.update(data)
        self._data.update(kwargs)
        return self

    # ── conditionals ─────────────────────────────────────────────────────────

    def when(
        self,
        value: Any,
        callback: Callable[..., Any],
        default: Optional[Callable[..., Any]] = None,
    ) -> "PushBuilder":
        """Apply ``callback`` only when ``value`` is truthy — Laravel's ``when()``;
        otherwise ``default`` runs, if given. Callbacks take ``(builder, value)``
        or just ``(builder)``. A callable ``value`` is called with the builder
        first. Returns the builder, whatever the callbacks return.
        """
        if callable(value):
            value = value(self)
        if value:
            _apply(callback, self, value)
        elif default is not None:
            _apply(default, self, value)
        return self

    def unless(
        self,
        value: Any,
        callback: Callable[..., Any],
        default: Optional[Callable[..., Any]] = None,
    ) -> "PushBuilder":
        """The inverse of :meth:`when`: apply ``callback`` only when ``value`` is falsy."""
        if callable(value):
            value = value(self)
        if not value:
            _apply(callback, self, value)
        elif default is not None:
            _apply(default, self, value)
        return self

    # ── scheduling ───────────────────────────────────────────────────────────

    def send_at(self, at: Any) -> "PushBuilder":
        """Deliver at this time instead of now: an aware ``datetime``, epoch seconds,
        or an ISO-8601 string with an offset (up to a year ahead; a past time goes
        out within a minute). ``send()`` then returns the scheduled send. ``None``
        clears it, so ``.send_at(maybe)`` sends now when there is no time.
        """
        self._send_at = None if at is None or at == "" else send_at_value(at)
        return self

    def name(self, label: Optional[str]) -> "PushBuilder":
        """Label shown for a scheduled send in the console."""
        self._name = label or None
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
        """Empty ids are ignored, so ``to_users([])`` adds no target."""
        for distinct_id in distinct_ids:
            if distinct_id and distinct_id not in self._distinct_ids:
                self._distinct_ids.append(distinct_id)
        return self

    def to_user(self, distinct_id: str) -> "PushBuilder":
        return self.to_users([distinct_id])

    def to_segments(self, segment_ids: Iterable[str]) -> "PushBuilder":
        for segment_id in segment_ids:
            if segment_id and segment_id not in self._segment_ids:
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
        title, body, localizations, default_language = self._content()
        return compact(
            {
                "title": title,
                "body": body,
                "localizations": localizations,
                "defaultLanguage": default_language,
                "imageUrl": self._image_url,
                "data": self._data or None,
                "options": self._options or None,
                "distinctIds": self._distinct_ids or None,
                "segmentIds": self._segment_ids or None,
                "filter": self._filter or None,
                "all": True if self._all else None,
                "sendAt": self._send_at,
                "name": self._name if self._send_at else None,
            }
        )

    def _content(self) -> "tuple[Optional[str], Optional[str], Optional[Dict[str, Dict[str, str]]], Optional[str]]":
        """Base text (the default language's, when given per language) plus one
        complete ``{title, body}`` per language — a language missing one takes the
        default language's."""
        langs = list(dict.fromkeys([*self._titles, *self._bodies]))
        if not langs:
            return self._title, self._body, None, None
        default = self._default_language or ("en" if "en" in langs else langs[0])
        title = self._title or self._titles.get(default) or next(iter(self._titles.values()), None)
        body = self._body or self._bodies.get(default)
        localizations: Dict[str, Dict[str, str]] = {}
        for lang in langs:
            t = self._titles.get(lang) or title
            if not t:
                continue
            b = self._bodies.get(lang) or body
            localizations[lang] = {"title": t, "body": b} if b else {"title": t}
        return title, body, localizations or None, default

    def _validate(self) -> Dict[str, Any]:
        if not self.to_dict().get("title"):
            raise ValueError("A push notification requires a title().")
        if not (self._all or self._distinct_ids or self._segment_ids or self._filter):
            raise ValueError(
                "A push notification requires a target: to_users(), to_segments(), where(), or to_all()."
            )
        return self.to_dict()

    def send(self) -> Dict[str, Any]:
        """Deliver it. Returns ``{sent, failed, recipients}`` — or, with
        :meth:`send_at`, the scheduled send ``{id, status, scheduledAt, …}``."""
        return self._sender(self._validate())

    def schedule(self, at: Any) -> Dict[str, Any]:
        """Shorthand for ``.send_at(at).send()``."""
        return self.send_at(at).send()


def _expand_translations(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn a plain-call payload whose ``title``/``body`` are per-language maps
    into the wire shape, the same way the builder does. Plain strings pass through."""
    title, body = payload.get("title"), payload.get("body")
    if not isinstance(title, Mapping) and not isinstance(body, Mapping):
        return dict(payload)
    b = PushBuilder(lambda _p: {})
    b.title(title if title is not None else "")
    b.body(body)
    b.default_language(payload.get("defaultLanguage"))
    content = {k: v for k, v in b.to_dict().items() if k in ("title", "body", "localizations", "defaultLanguage")}
    rest = {k: v for k, v in payload.items() if k not in ("title", "body", "defaultLanguage")}
    return {**rest, **content}
