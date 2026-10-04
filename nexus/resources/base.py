"""Shared plumbing for the resource classes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .._payloads import Request
    from ..client import NexusClient

__all__ = ["Resource", "CTX_ALIASES", "to_wire_context"]

#: snake_case keyword → the camelCase field the API expects.
CTX_ALIASES = {
    "distinct_id": "distinctId",
    "session_key": "sessionKey",
    "device_key": "deviceKey",
    "release": "release",
    "app_version": "appVersion",
    "os_type": "osType",
    "os_version": "osVersion",
    "browser": "browser",
    "country": "country",
    "url": "url",
    "entry_url": "entryUrl",
    "device_type": "deviceType",
    "app_build": "appBuild",
    "app_instance_id": "appInstanceId",
    "user_properties": "userProperties",
    "first_open_time": "firstOpenTime",
    "device_id": "deviceId",
    "click_id": "clickId",
    "iteration_key": "iterationKey",
    "response_id": "responseId",
    "recording_id": "recordingId",
    "message_id": "messageId",
    "button_id": "buttonId",
    "session_id": "sessionId",
    "leg_id": "legId",
    "identity_id": "identityId",
    "caller_id": "callerId",
    "endpoint_type": "endpointType",
}


def to_wire_context(values: Mapping[str, Any]) -> Dict[str, Any]:
    """Translate snake_case kwargs to the API's camelCase field names."""
    return {CTX_ALIASES.get(key, key): value for key, value in values.items()}


class Resource:
    """Base for every resource namespace hanging off the client."""

    def __init__(self, client: "NexusClient") -> None:
        self._client = client

    # ── helpers ──────────────────────────────────────────────────────────────

    def _send(self, request: "Request") -> Any:
        return self._client.request(request)

    def _context(self, **overrides: Any) -> Dict[str, Any]:
        """Config defaults < ambient context < explicit keyword overrides."""
        return self._client.resolve_context(**to_wire_context(overrides))

    @staticmethod
    def _dict(value: Any) -> Dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _list(value: Any, key: Optional[str] = None) -> list:
        if isinstance(value, Mapping) and key is not None:
            value = value.get(key)
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _written(result: Any, fallback: int) -> int:
        if isinstance(result, Mapping):
            written = result.get("written")
            if isinstance(written, int):
                return written
        return fallback
