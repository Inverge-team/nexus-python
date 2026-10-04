"""Remote Config — server-evaluated, publishable configuration.

Conditions (platform, version, country, percentile, custom user attributes) are
evaluated server-side; the SDK receives final values. On the backend this is how
you change behaviour — a phone number, a fee, a copy string, a kill switch —
without a deploy.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Mapping, Optional

from .. import _payloads as p
from ..models import RemoteConfigSnapshot
from .base import Resource

__all__ = ["RemoteConfig"]

_VALUE_TYPES = ("STRING", "NUMBER", "BOOLEAN", "JSON")


def infer_value_type(value: Any) -> str:
    """Map a Python value to a Remote Config value type."""
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float)):
        return "NUMBER"
    if isinstance(value, (dict, list)):
        return "JSON"
    return "STRING"


class RemoteConfig(Resource):
    """``client.remote_config``

    Reads are cached in-process for ``cache_ttl`` seconds (default 30) and
    revalidated with the previous ETag, so a hot path can call
    :meth:`get` freely. Pass ``fresh=True`` to bypass the cache.
    """

    def __init__(self, client: Any, *, cache_ttl: float = 30.0) -> None:
        super().__init__(client)
        self.cache_ttl = cache_ttl
        self._defaults: Dict[str, Any] = {}
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # ── defaults ─────────────────────────────────────────────────────────────

    def set_defaults(self, defaults: Mapping[str, Any]) -> None:
        """In-app fallbacks used when a key is absent or the fetch fails."""
        self._defaults.update(defaults)

    @property
    def defaults(self) -> Mapping[str, Any]:
        return dict(self._defaults)

    # ── read ─────────────────────────────────────────────────────────────────

    def fetch(
        self,
        context: Optional[Mapping[str, Any]] = None,
        *,
        etag: Optional[str] = None,
        fresh: bool = False,
        app_instance_id: Optional[str] = None,
        app_version: Optional[str] = None,
        app_build: Optional[int] = None,
        platform: Optional[str] = None,
        os_version: Optional[str] = None,
        country: Optional[str] = None,
        language: Optional[str] = None,
        first_open_time: Optional[str] = None,
        user_properties: Optional[Mapping[str, Any]] = None,
    ) -> RemoteConfigSnapshot:
        """Resolve the active template for a context.

        ``user_properties`` feeds "Custom attribute" conditions; values must be
        primitives (str/int/float/bool) and match the condition value exactly.

        ``platform`` defaults to ``"server"``. Pass the real platform when
        resolving config *on behalf of* a device, or platform conditions will
        not match.
        """
        resolved = self._resolve_context(
            context,
            app_instance_id=app_instance_id,
            app_version=app_version,
            app_build=app_build,
            platform=platform,
            os_version=os_version,
            country=country,
            language=language,
            first_open_time=first_open_time,
            user_properties=user_properties,
        )
        key = repr(sorted(resolved.items()))

        with self._lock:
            entry = self._cache.get(key)
        if entry and not fresh and etag is None and (time.monotonic() - entry["at"]) < self.cache_ttl:
            return entry["snapshot"]

        previous: Optional[RemoteConfigSnapshot] = entry["snapshot"] if entry else None
        conditional_etag = etag if etag is not None else (previous.etag if previous else None)

        payload = self._send(p.remote_config_fetch(resolved, conditional_etag or None))
        snapshot = RemoteConfigSnapshot.from_response(
            payload, defaults=self._defaults, previous=previous
        )
        if payload is None and previous is not None:
            # Silent mode swallowed a failure — keep serving the last good copy.
            return previous

        with self._lock:
            self._cache[key] = {"snapshot": snapshot, "at": time.monotonic()}
        return snapshot

    def all(self, context: Optional[Mapping[str, Any]] = None, **options: Any) -> Dict[str, Any]:
        """Flat ``{key: value}`` map, in-app defaults merged in."""
        return self.fetch(context, **options).to_dict()

    def get(
        self,
        key: str,
        default: Any = None,
        context: Optional[Mapping[str, Any]] = None,
        **options: Any,
    ) -> Any:
        """One parameter's resolved value, or ``default``."""
        return self.fetch(context, **options).get(key, default)

    def get_string(self, key: str, default: str = "", context: Optional[Mapping[str, Any]] = None, **o: Any) -> str:
        return self.fetch(context, **o).get_string(key, default)

    def get_bool(self, key: str, default: bool = False, context: Optional[Mapping[str, Any]] = None, **o: Any) -> bool:
        return self.fetch(context, **o).get_bool(key, default)

    def get_number(
        self, key: str, default: float = 0.0, context: Optional[Mapping[str, Any]] = None, **o: Any
    ) -> float:
        return self.fetch(context, **o).get_number(key, default)

    def get_int(self, key: str, default: int = 0, context: Optional[Mapping[str, Any]] = None, **o: Any) -> int:
        return self.fetch(context, **o).get_int(key, default)

    def get_json(self, key: str, default: Any = None, context: Optional[Mapping[str, Any]] = None, **o: Any) -> Any:
        return self.fetch(context, **o).get_json(key, default)

    def invalidate(self) -> None:
        """Drop the in-process cache (next read refetches)."""
        with self._lock:
            self._cache.clear()

    # ── write (draft → publish) ──────────────────────────────────────────────

    def set_parameter(
        self,
        key: str,
        value: Any = None,
        *,
        value_type: Optional[str] = None,
        conditional_values: Optional[Mapping[str, Any]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create/update a parameter in the **draft** template.

        Nothing changes for clients until :meth:`publish`.
        """
        resolved_type = (value_type or infer_value_type(value)).upper()
        if resolved_type not in _VALUE_TYPES:
            raise ValueError("value_type must be one of {0}".format(", ".join(_VALUE_TYPES)))
        return self._dict(
            self._send(
                p.remote_config_set_parameter(key, resolved_type, value, conditional_values, description)
            )
        )

    def delete_parameter(self, key: str) -> None:
        """Remove a parameter from the draft template."""
        self._send(p.remote_config_delete_parameter(key))

    def publish(self, description: Optional[str] = None) -> Dict[str, Any]:
        """Make the draft live. Returns ``{versionNumber, etag, createdAt}``."""
        result = self._dict(self._send(p.remote_config_publish(description)))
        self.invalidate()
        return result

    def set(
        self,
        key: str,
        value: Any,
        *,
        value_type: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set one parameter and publish it in a single step."""
        self.set_parameter(key, value, value_type=value_type, description=description)
        return self.publish(description)

    # ── internals ────────────────────────────────────────────────────────────

    def _resolve_context(self, context: Optional[Mapping[str, Any]], **overrides: Any) -> Dict[str, Any]:
        from .base import to_wire_context

        merged: Dict[str, Any] = {}
        if context:
            merged.update(to_wire_context(context))
        merged.update({k: v for k, v in to_wire_context(overrides).items() if v is not None})

        config = self._client.config
        merged.setdefault("platform", "server")
        if config.app_version:
            merged.setdefault("appVersion", config.app_version)
        if config.os_version:
            merged.setdefault("osVersion", config.os_version)

        ambient = self._client.resolve_context()
        for wire_key in ("appVersion", "osVersion", "country"):
            if ambient.get(wire_key):
                merged.setdefault(wire_key, ambient[wire_key])

        if merged.get("userProperties"):
            merged["userProperties"] = dict(merged["userProperties"])
        return {k: v for k, v in merged.items() if v is not None}
