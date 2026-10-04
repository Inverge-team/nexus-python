"""Fluent builder for a Live Activity."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

__all__ = ["LiveActivityBuilder"]

Starter = Callable[..., Dict[str, Any]]


class LiveActivityBuilder:
    """Compose the content-state and targeting, then ``start()`` / ``update()`` / ``end()``.

    ::

        (client.live_activities.activity("DeliveryAttributes", "order_42")
            .title("Order #42").status("Preparing").progress(20)
            .to_user("user_1").priority(10)
            .start())

        client.live_activities.activity("order_42").status("On the way").progress(70).update()
        client.live_activities.activity("order_42").status("Delivered").end()

    ``activity(type, id)`` for a start; ``activity(id)`` for update/end.
    """

    def __init__(
        self,
        start_fn: Starter,
        update_fn: Starter,
        end_fn: Starter,
        activity_id: str,
        activity_type: Optional[str] = None,
    ) -> None:
        self._start_fn = start_fn
        self._update_fn = update_fn
        self._end_fn = end_fn
        self._activity_id = activity_id
        self._activity_type = activity_type
        self._state: Dict[str, Any] = {}
        self._distinct_ids: List[str] = []
        self._attributes: Dict[str, Any] = {}
        self._priority: Optional[int] = None
        self._stale_date: Optional[str] = None
        self._dismissal_date: Optional[str] = None

    # ── content state ────────────────────────────────────────────────────────

    def title(self, value: str) -> "LiveActivityBuilder":
        return self.set("title", value)

    def subtitle(self, value: str) -> "LiveActivityBuilder":
        return self.set("subtitle", value)

    def body(self, value: str) -> "LiveActivityBuilder":
        return self.set("body", value)

    def status(self, value: str) -> "LiveActivityBuilder":
        return self.set("status", value)

    def progress(self, value: float) -> "LiveActivityBuilder":
        """0–100 or 0.0–1.0 — both are accepted by the renderers."""
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
        """Start the activity. Returns ``{ios, android}`` device counts."""
        if not self._activity_type:
            raise ValueError("start() requires an activity type: activity(type, id).")
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
        """Push a new content state to every device on this activity."""
        return self._update_fn(
            self._activity_id,
            content_state=self._state,
            stale_date=self._stale_date,
            priority=self._priority,
        )

    def end(self) -> Dict[str, Any]:
        """End it, with an optional final state and dismissal time."""
        return self._end_fn(
            self._activity_id,
            content_state=self._state or None,
            dismissal_date=self._dismissal_date,
        )
