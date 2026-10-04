"""Session replay ingestion.

Replay is recorded by the browser SDK (rrweb) and by the mobile SDKs. This
resource exists for proxying those chunks through your own backend — e.g. when
the browser must not talk to Nexus directly — and for replaying synthetic
sessions from a worker.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from .. import _payloads as p
from .base import Resource

__all__ = ["Replay"]


class Replay(Resource):
    """``client.replay``"""

    def ingest(
        self,
        recording_id: str,
        events: Sequence[Mapping[str, Any]],
        *,
        distinct_id: Optional[str] = None,
        session_key: Optional[str] = None,
        device_key: Optional[str] = None,
        href: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        browser: Optional[str] = None,
        app_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ship one chunk of replay events for a recording.

        ``recording_id`` must stay stable across every chunk of a recording.
        At most 5000 events per call.
        """
        items = [dict(e) for e in events]
        if not items:
            return {}
        if len(items) > 5000:
            raise ValueError("replay.ingest() accepts at most 5000 events per chunk.")
        fields = self._context(
            distinct_id=distinct_id,
            session_key=session_key,
            device_key=device_key,
            os_type=os_type,
            os_version=os_version,
            browser=browser,
            app_version=app_version,
            url=href,
        )
        fields["recordingId"] = recording_id
        fields["href"] = href or fields.get("url")
        fields["width"] = width
        fields["height"] = height
        return self._dict(self._send(p.replay_ingest(items, fields)))
