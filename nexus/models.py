"""Small result objects the SDK returns where a raw dict would be clumsy.

Everything else comes back as the API's own decoded JSON (``dict`` / ``list``),
so new server fields are available immediately without an SDK upgrade.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

__all__ = ["FlagEvaluation", "RemoteConfigSnapshot", "RemoteConfigValue"]


@dataclass(frozen=True)
class FlagEvaluation:
    """Every active flag resolved for one person.

    ``flags`` maps a key to ``True``/``False`` (boolean flags) or to the
    resolved variant name (multivariate flags).
    """

    flags: Mapping[str, Any] = field(default_factory=dict)
    payloads: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, payload: Any) -> "FlagEvaluation":
        data = payload if isinstance(payload, Mapping) else {}
        flags = data.get("flags")
        payloads = data.get("payloads")
        return cls(
            flags=dict(flags) if isinstance(flags, Mapping) else {},
            payloads=dict(payloads) if isinstance(payloads, Mapping) else {},
        )

    def is_enabled(self, key: str) -> bool:
        """``True`` for an on boolean flag, or for any resolved variant."""
        value = self.flags.get(key, False)
        return value is True or (isinstance(value, str) and value != "")

    def variant(self, key: str) -> Optional[str]:
        """The variant name for a multivariate flag, else ``None``."""
        value = self.flags.get(key)
        return value if isinstance(value, str) else None

    def payload(self, key: str, default: Any = None) -> Any:
        """The flag's attached JSON payload."""
        return self.payloads.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self.flags

    def __getitem__(self, key: str) -> Any:
        return self.flags[key]

    def to_dict(self) -> Dict[str, Any]:
        return {"flags": dict(self.flags), "payloads": dict(self.payloads)}


@dataclass(frozen=True)
class RemoteConfigValue:
    """One resolved Remote Config parameter."""

    key: str
    value: Any = None
    value_type: Optional[str] = None
    #: Which condition supplied the value (``None`` → the default value).
    source: Optional[str] = None

    def as_string(self, default: str = "") -> str:
        if self.value is None:
            return default
        if isinstance(self.value, str):
            return self.value
        if isinstance(self.value, bool):
            return "true" if self.value else "false"
        if isinstance(self.value, (int, float)):
            return str(self.value)
        try:
            return json.dumps(self.value)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return default

    def as_bool(self, default: bool = False) -> bool:
        value = self.value
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "y", "t"}
        return default

    def as_number(self, default: float = 0.0) -> float:
        value = self.value
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return default
        return default

    def as_int(self, default: int = 0) -> int:
        try:
            return int(self.as_number(float(default)))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return default

    def as_json(self, default: Any = None) -> Any:
        value = self.value
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return default
        return default if value is None else value


@dataclass(frozen=True)
class RemoteConfigSnapshot:
    """A resolved Remote Config template: typed getters plus the raw payload."""

    version: int = 0
    etag: str = ""
    not_modified: bool = False
    throttled: bool = False
    parameters: Mapping[str, RemoteConfigValue] = field(default_factory=dict)
    defaults: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(
        cls,
        payload: Any,
        *,
        defaults: Optional[Mapping[str, Any]] = None,
        previous: Optional["RemoteConfigSnapshot"] = None,
    ) -> "RemoteConfigSnapshot":
        data = payload if isinstance(payload, Mapping) else {}
        not_modified = bool(data.get("notModified"))
        if not_modified and previous is not None:
            # Nothing changed server-side — keep the values we already resolved.
            return RemoteConfigSnapshot(
                version=previous.version,
                etag=previous.etag,
                not_modified=True,
                throttled=bool(data.get("throttled")),
                parameters=previous.parameters,
                defaults=dict(defaults) if defaults is not None else previous.defaults,
                raw=dict(data),
            )

        parameters: Dict[str, RemoteConfigValue] = {}
        raw_params = data.get("parameters")
        if isinstance(raw_params, Mapping):
            for key, entry in raw_params.items():
                if isinstance(entry, Mapping):
                    parameters[key] = RemoteConfigValue(
                        key=key,
                        value=entry.get("value"),
                        value_type=entry.get("valueType"),
                        source=entry.get("source"),
                    )
                else:  # tolerate a flat {key: value} shape
                    parameters[key] = RemoteConfigValue(key=key, value=entry)

        return cls(
            version=int(data.get("version") or 0),
            etag=str(data.get("etag") or ""),
            not_modified=not_modified,
            throttled=bool(data.get("throttled")),
            parameters=parameters,
            defaults=dict(defaults or {}),
            raw=dict(data),
        )

    # ── access ───────────────────────────────────────────────────────────────

    def value(self, key: str) -> RemoteConfigValue:
        """The parameter, falling back to the in-app default you registered."""
        found = self.parameters.get(key)
        if found is not None:
            return found
        return RemoteConfigValue(key=key, value=self.defaults.get(key), source=None)

    def get(self, key: str, default: Any = None) -> Any:
        found = self.parameters.get(key)
        if found is not None and found.value is not None:
            return found.value
        if key in self.defaults:
            return self.defaults[key]
        return default

    def get_string(self, key: str, default: str = "") -> str:
        return self.value(key).as_string(default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        return self.value(key).as_bool(default)

    def get_number(self, key: str, default: float = 0.0) -> float:
        return self.value(key).as_number(default)

    def get_int(self, key: str, default: int = 0) -> int:
        return self.value(key).as_int(default)

    def get_json(self, key: str, default: Any = None) -> Any:
        return self.value(key).as_json(default)

    def source_of(self, key: str) -> Optional[str]:
        """Name of the condition that supplied the value, or ``None``."""
        found = self.parameters.get(key)
        return found.source if found else None

    def to_dict(self) -> Dict[str, Any]:
        """Flat ``{key: value}`` map, defaults included."""
        out: Dict[str, Any] = dict(self.defaults)
        for key, entry in self.parameters.items():
            out[key] = entry.value
        return out

    def __contains__(self, key: object) -> bool:
        return key in self.parameters or key in self.defaults

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def keys(self) -> Any:
        return self.to_dict().keys()

    def items(self) -> Any:
        return self.to_dict().items()
