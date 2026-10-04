"""Deep links & attribution — record a conversion against a link click."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .. import _payloads as p
from .base import Resource

__all__ = ["Links"]


class Links(Resource):
    """``client.links``"""

    def attribute(
        self,
        type: str = "install",  # noqa: A002 - mirrors the API field name
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
        """Record an attribution event.

        ``type`` is one of ``install``, ``open``, ``reengagement``,
        ``uninstall``, ``in_app``.

        ::

            client.links.attribute("install", click_id="abc", name="summer_sale",
                                   platform="ios", distinct_id="user_1")
        """
        fields = self._context(
            distinct_id=distinct_id,
            session_key=session_key,
            os_type=os_type,
            country=country,
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
        return self._dict(self._send(p.links_attribute(fields)))

    def install(self, **options: Any) -> Dict[str, Any]:
        """Shorthand for ``attribute("install", ...)``."""
        return self.attribute("install", **options)

    def open(self, **options: Any) -> Dict[str, Any]:
        """Shorthand for ``attribute("open", ...)``."""
        return self.attribute("open", **options)

    def reengagement(self, **options: Any) -> Dict[str, Any]:
        """Shorthand for ``attribute("reengagement", ...)``."""
        return self.attribute("reengagement", **options)
