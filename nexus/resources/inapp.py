"""In-app messages — modals and banners composed in the console.

The client SDKs render them. Server-side you fetch what a user is eligible for
(headless / server-driven UIs) and record impressions, clicks and dismissals.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import _payloads as p
from .base import Resource

__all__ = ["InApp"]


class InApp(Resource):
    """``client.inapp``"""

    def active(
        self, *, distinct_id: Optional[str] = None, device_key: Optional[str] = None
    ) -> List[Any]:
        """The tenant's active in-app messages."""
        ctx = self._context(distinct_id=distinct_id, device_key=device_key)
        return self._list(
            self._send(p.inapp_active(ctx.get("distinctId"), ctx.get("deviceKey"))), "messages"
        )

    def track_event(
        self,
        message_id: str,
        type: str,  # noqa: A002 - mirrors the API field name
        *,
        button_id: Optional[str] = None,
        distinct_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record ``impression``, ``click`` or ``dismiss``."""
        ctx = self._context(distinct_id=distinct_id)
        return self._dict(
            self._send(p.inapp_event(message_id, type, button_id, ctx.get("distinctId")))
        )

    def impression(self, message_id: str, **options: Any) -> Dict[str, Any]:
        return self.track_event(message_id, "impression", **options)

    def click(self, message_id: str, button_id: str, **options: Any) -> Dict[str, Any]:
        return self.track_event(message_id, "click", button_id=button_id, **options)

    def dismiss(self, message_id: str, **options: Any) -> Dict[str, Any]:
        return self.track_event(message_id, "dismiss", **options)
