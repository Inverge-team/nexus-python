"""Fluent builder for a Live Activity."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Union

__all__ = ["LiveActivityBuilder"]

Starter = Callable[..., Dict[str, Any]]


def _unix_seconds(at: Union[_dt.datetime, int, float]) -> int:
    """datetime (naive = UTC) / unix seconds / unix ms → unix seconds."""
    if isinstance(at, _dt.datetime):
        if at.tzinfo is None:
            at = at.replace(tzinfo=_dt.timezone.utc)
        return int(round(at.timestamp()))
    return int(round(at / 1000)) if at > 1e11 else int(round(at))


class LiveActivityBuilder:
    """Compose the data / content-state and targeting, then ``start()`` / ``update()`` / ``end()``.

    Templated (recommended) — the design is a template made in the Nexus console;
    the mobile SDKs render it on iOS + Android in each device's language. Send
    only data; updates are partial and merged server-side::

        (client.live_activities.template("order_tracking", "order_42")
            .step("pending").eta_at(eta).values(driver="Ahmed")
            .to_user("user_1")
            .start())

        client.live_activities.activity("order_42").step("arrived").update()
        client.live_activities.activity("order_42").step("completed").end()

    Plain (your own iOS widget)::

        (client.live_activities.activity("DeliveryAttributes", "order_42")
            .title("Order #42").status("Preparing").progress(20)
            .to_user("user_1").priority(10)
            .start())

        client.live_activities.activity("order_42").status("On the way").progress(70).update()
        client.live_activities.activity("order_42").status("Delivered").end()

    ``template(key, id)`` / ``activity(type, id)`` for a start; ``activity(id)``
    for update/end. Template-only setters (``step``, ``eta_at``, ``value``,
    ``canceled``, …) send ``data``; on their own, ``progress`` and the texts keep
    the plain ``contentState`` behaviour.
    """

    def __init__(
        self,
        start_fn: Starter,
        update_fn: Starter,
        end_fn: Starter,
        activity_id: str,
        activity_type: Optional[str] = None,
        *,
        template: Optional[Union[str, Mapping[str, Any]]] = None,
        start_template_fn: Optional[Starter] = None,
        update_data_fn: Optional[Starter] = None,
    ) -> None:
        self._start_fn = start_fn
        self._update_fn = update_fn
        self._end_fn = end_fn
        self._start_template_fn = start_template_fn
        self._update_data_fn = update_data_fn
        self._activity_id = activity_id
        self._activity_type = activity_type
        self._template = template
        self._data: Dict[str, Any] = {}
        self._lang: Optional[str] = None
        self._alert: Optional[Dict[str, str]] = None
        self._state: Dict[str, Any] = {}
        self._distinct_ids: List[str] = []
        self._attributes: Dict[str, Any] = {}
        self._priority: Optional[int] = None
        self._stale_date: Optional[str] = None
        self._dismissal_date: Optional[str] = None

    @property
    def _templated(self) -> bool:
        d = self._data
        return self._template is not None or any(k in d for k in ("step", "state", "etaAt", "values"))

    # ── template data ────────────────────────────────────────────────────────

    def step(self, step: str) -> "LiveActivityBuilder":
        """The current step (a step id or label of the template)."""
        self._data["step"] = step
        return self

    def eta_at(self, at: Union[_dt.datetime, int, float]) -> "LiveActivityBuilder":
        """Arrival time → a live countdown on the device (datetime, unix s or ms)."""
        self._data["etaAt"] = _unix_seconds(at)
        return self

    def eta(self, text: str) -> "LiveActivityBuilder":
        """A fixed ETA text instead of a countdown, e.g. ``"8 min"``."""
        return self.value("eta", text)

    def value(self, key: str, value: Any) -> "LiveActivityBuilder":
        """One ``{{placeholder}}`` value."""
        self._data.setdefault("values", {})[key] = value
        return self

    def values(self, values: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> "LiveActivityBuilder":
        merged = dict(values or {})
        merged.update(kwargs)
        self._data.setdefault("values", {}).update(merged)
        return self

    def canceled(self) -> "LiveActivityBuilder":
        """Show the canceled state."""
        self._data["state"] = "canceled"
        return self

    def done(self) -> "LiveActivityBuilder":
        """Show the finished state."""
        self._data["state"] = "done"
        return self

    def data(self, data: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> "LiveActivityBuilder":
        """Merge raw template data (``step``, ``state``, ``progress``, ``etaAt``, ``values``)."""
        merged = dict(data or {})
        merged.update(kwargs)
        values = merged.pop("values", None)
        self._data.update(merged)
        if values:
            self._data.setdefault("values", {}).update(values)
        return self

    def lang(self, lang: str) -> "LiveActivityBuilder":
        """Start only: force one language for every recipient (default: each device's own)."""
        self._lang = lang
        return self

    def alert(self, title: str, body: Optional[str] = None) -> "LiveActivityBuilder":
        """Start only: the iOS alert shown with the start."""
        self._alert = {"title": title}
        if body:
            self._alert["body"] = body
        return self

    # ── content state (templated: these override the template's texts) ──────

    def title(self, value: str) -> "LiveActivityBuilder":
        return self._text("title", value)

    def subtitle(self, value: str) -> "LiveActivityBuilder":
        return self._text("subtitle", value)

    def body(self, value: str) -> "LiveActivityBuilder":
        return self._text("body", value)

    def _text(self, key: str, value: str) -> "LiveActivityBuilder":
        self._data[key] = value
        return self.set(key, value)

    def status(self, value: str) -> "LiveActivityBuilder":
        return self.set("status", value)

    def progress(self, value: float) -> "LiveActivityBuilder":
        """0.0–1.0 or 0–100. Templated: toward the next step / along the bar."""
        self._data["progress"] = value
        return self.set("progress", value)

    def set(self, key: str, value: Any) -> "LiveActivityBuilder":
        """Set any content-state field."""
        self._state[key] = value
        return self

    def state(self, state: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> "LiveActivityBuilder":
        if state:
            self._state.update(state)
        self._state.update(kwargs)
        return self

    # ── targeting (start only) ───────────────────────────────────────────────

    def to_users(self, distinct_ids: Iterable[str]) -> "LiveActivityBuilder":
        for distinct_id in distinct_ids:
            if distinct_id not in self._distinct_ids:
                self._distinct_ids.append(distinct_id)
        return self

    def to_user(self, distinct_id: str) -> "LiveActivityBuilder":
        return self.to_users([distinct_id])

    def shared(self) -> "LiveActivityBuilder":
        """One activity many people watch (a match, a launch, an incident)."""
        self._distinct_ids = []
        return self

    def attributes(self, attributes: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> "LiveActivityBuilder":
        """Fixed attributes set at start (iOS ActivityKit)."""
        if attributes:
            self._attributes.update(attributes)
        self._attributes.update(kwargs)
        return self

    # ── modifiers ────────────────────────────────────────────────────────────

    def priority(self, priority: int) -> "LiveActivityBuilder":
        """APNs priority: 5 = routine (unmetered), 10 = immediate (metered)."""
        self._priority = int(priority)
        return self

    def stale_at(self, iso: str) -> "LiveActivityBuilder":
        """ISO-8601 — mark the activity stale after this time."""
        self._stale_date = iso
        return self

    def dismiss_at(self, iso: str) -> "LiveActivityBuilder":
        """ISO-8601 — when iOS should remove the activity (end only)."""
        self._dismissal_date = iso
        return self

    # ── terminal ─────────────────────────────────────────────────────────────

    def start(self) -> Dict[str, Any]:
        """Start the activity. Returns ``{ios, android, iosTargets, iosWarning?}``."""
        if self._template is not None:
            if self._start_template_fn is None:
                raise ValueError("this client cannot start templated activities")
            return self._start_template_fn(
                self._template,
                self._activity_id,
                self._data,
                distinct_ids=self._distinct_ids or None,
                lang=self._lang,
                alert=self._alert,
                attributes=self._attributes or None,
                stale_date=self._stale_date,
                priority=self._priority,
            )
        if not self._activity_type:
            raise ValueError(
                "start() requires a template — template(key, id) — or an activity type — activity(type, id)."
            )
        return self._start_fn(
            self._activity_type,
            self._activity_id,
            content_state=self._state,
            distinct_ids=self._distinct_ids or None,
            attributes=self._attributes or None,
            stale_date=self._stale_date,
            priority=self._priority,
        )

    def update(self) -> Dict[str, Any]:
        """Push the change to every device on this activity (templated: partial data, merged)."""
        if self._templated and self._update_data_fn is not None:
            return self._update_data_fn(
                self._activity_id, self._data, stale_date=self._stale_date, priority=self._priority
            )
        return self._update_fn(
            self._activity_id,
            content_state=self._state,
            stale_date=self._stale_date,
            priority=self._priority,
        )

    def end(self) -> Dict[str, Any]:
        """End it, with an optional final state and dismissal time."""
        if self._templated:
            return self._end_fn(
                self._activity_id, data=self._data or None, dismissal_date=self._dismissal_date
            )
        return self._end_fn(
            self._activity_id,
            content_state=self._state or None,
            dismissal_date=self._dismissal_date,
        )
