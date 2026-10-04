"""The sessions spine — identify people, start/refresh journey sessions.

Events, logs, errors, surveys and replay all correlate into a session, so this
is usually the first call a backend makes for a request.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .. import _payloads as p
from .base import Resource

__all__ = ["Sessions"]


class Sessions(Resource):
    def identify(
        self,
        distinct_id: str,
        *,
        email: Optional[str] = None,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        traits: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or update the person behind ``distinct_id``.

        ::

            client.sessions.identify("user_123", email="a@b.com", traits={"plan": "pro"})
        """
        return self._dict(self._send(p.sessions_identify(distinct_id, email, name, phone, traits)))

    def track(
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
        """Start or refresh the active session; returns the server payload
        (including ``sessionId``).

        Omitted identity fields fall back to the ambient
        :mod:`nexus.context` and then to the client config.
        """
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
        return self._dict(self._send(p.sessions_track(fields)))
