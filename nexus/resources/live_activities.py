"""Live Activities — a live, updating view of an in-progress event.

iOS renders it on the Lock Screen / Dynamic Island; Android as an ongoing live
notification. The classic server-side shape: start when the order/ride/match
begins, update as its status changes, end when it's done.

iOS needs the APNs key configured in the console (Live Activities cannot go
through FCM) and a Widget Extension in the app. Android needs no extra setup.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from .. import _payloads as p
from ..builders.live_activity import LiveActivityBuilder
from .base import Resource

__all__ = ["LiveActivities"]


class LiveActivities(Resource):
    """``client.live_activities``"""

    def activity(self, activity_type_or_id: str, activity_id: Optional[str] = None) -> LiveActivityBuilder:
        """Start a fluent activity.

        ``activity(type, id)`` to start one; ``activity(id)`` to update or end
        an existing one (the type is only needed at start).
        """
        if activity_id is None:
            return LiveActivityBuilder(self.start, self.update, self.end, activity_type_or_id)
        return LiveActivityBuilder(self.start, self.update, self.end, activity_id, activity_type_or_id)

    def start(
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
        """Start an activity. Omit ``distinct_ids`` for a shared activity.

        Returns ``{ios, android}`` — how many devices were reached.
        """
        return self._dict(
            self._send(
                p.live_activity_start(
                    activity_type, activity_id, content_state, distinct_ids, attributes, stale_date, priority
                )
            )
        )

    def update(
        self,
        activity_id: str,
        content_state: Optional[Mapping[str, Any]] = None,
        *,
        stale_date: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Push a new content state. ``priority`` 5 = routine (unmetered),
        10 = immediate (metered)."""
        return self._dict(
            self._send(p.live_activity_update(activity_id, content_state, stale_date, priority))
        )

    def end(
        self,
        activity_id: str,
        content_state: Optional[Mapping[str, Any]] = None,
        *,
        dismissal_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """End the activity, optionally with a final state and dismissal time."""
        return self._dict(self._send(p.live_activity_end(activity_id, content_state, dismissal_date)))

    def register_push_to_start_token(
        self,
        activity_type: str,
        token: str,
        *,
        distinct_id: Optional[str] = None,
        device_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register an iOS push-to-start token (normally the client SDK's job)."""
        ctx = self._context(distinct_id=distinct_id, device_key=device_key)
        return self._dict(
            self._send(
                p.live_activity_push_token(
                    activity_type, token, ctx.get("distinctId"), ctx.get("deviceKey")
                )
            )
        )

    def register_activity(
        self,
        activity_id: str,
        activity_type: str,
        *,
        platform: str = "ios",
        update_token: Optional[str] = None,
        distinct_id: Optional[str] = None,
        content_state: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Register a running activity instance's update token."""
        ctx = self._context(distinct_id=distinct_id)
        return self._dict(
            self._send(
                p.live_activity_register(
                    activity_id, activity_type, platform, update_token, ctx.get("distinctId"), content_state
                )
            )
        )

    def event(self, activity_id: str, type: str, *, platform: Optional[str] = None) -> Dict[str, Any]:  # noqa: A002
        """Report a delivery receipt / click / failure for analytics."""
        return self._dict(self._send(p.live_activity_event(activity_id, type, platform)))
