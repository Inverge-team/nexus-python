"""SDK configuration.

:class:`NexusConfig` is an immutable value object. Build it explicitly, from a
mapping (``from_mapping`` — handy for Django's ``settings.NEXUS``), or from the
environment (``from_env``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Mapping, Optional

from . import _services
from ._version import __version__
from .exceptions import ConfigurationError

__all__ = ["NexusConfig", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = "https://services.inverge.net"

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f"}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class NexusConfig:
    """Everything the client needs to talk to Nexus.

    Args:
        api_key: Tenant API key (``nxs_...``). Required. It is scoped to **one
            environment**; every write lands in that environment.
        base_url: API origin. Point it at a local backend while developing.
        realtime_url: Socket.IO origin for :mod:`nexus.realtime_socket`.
            Defaults to ``base_url``.
        timeout: Per-request timeout in seconds.
        max_retries: Retries for *safe* requests (telemetry ingest, reads) on
            network errors, 429 and 5xx. Non-idempotent calls (push sends, voice
            control, realtime emits) are never retried.
        retry_backoff: Base delay in seconds; doubles per attempt.
        default_headers: Extra headers merged into every request.
        app_version / release / os_type / os_version: Default telemetry context,
            attached to events, logs and errors unless overridden per call.
        default_properties: Merged into the properties of every captured event.
        batch: Buffer events and logs and ship them on a background thread.
            Keeps telemetry off your request's hot path. Turn it off in short
            scripts and serverless functions if you prefer explicit sends
            (:meth:`nexus.NexusClient.flush` works either way).
        flush_interval: Seconds between background flushes.
        max_batch: Maximum items per batched request.
        max_queue: Buffer capacity; items beyond it are dropped (and counted)
            rather than growing memory without bound.
        silent: Never let a Nexus failure reach your code. Direct calls return
            ``None``/empty instead of raising, and the failure is logged to the
            ``nexus`` logger. Background delivery is always silent.
        debug: Verbose SDK logging on the ``nexus`` logger.
        user_agent: Overrides the default ``inverge-nexus-python/<version>``.
    """

    api_key: str
    base_url: str = DEFAULT_BASE_URL
    realtime_url: Optional[str] = None
    timeout: float = 10.0
    max_retries: int = 2
    retry_backoff: float = 0.25
    default_headers: Mapping[str, str] = field(default_factory=dict)

    app_version: Optional[str] = None
    release: Optional[str] = None
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    default_properties: Mapping[str, Any] = field(default_factory=dict)

    batch: bool = True
    flush_interval: float = 5.0
    max_batch: int = 50
    max_queue: int = 10000

    silent: bool = False
    debug: bool = False
    user_agent: Optional[str] = None

    #: Per-service switches, e.g. ``{"live_activities": False}`` (see :mod:`nexus._services`).
    services: Mapping[str, Any] = field(default_factory=dict)
    #: ``False`` switches every service off.
    enabled: bool = True
    #: Effective switched-off services → why (code or environment). Computed.
    services_off: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.api_key or not str(self.api_key).strip():
            raise ConfigurationError("A Nexus API key is required (nxs_...).")
        object.__setattr__(self, "api_key", str(self.api_key).strip())
        object.__setattr__(self, "base_url", str(self.base_url).rstrip("/"))
        if not self.base_url.startswith(("http://", "https://")):
            raise ConfigurationError(
                "base_url must start with http:// or https:// (got {0!r}).".format(self.base_url)
            )
        if self.realtime_url:
            object.__setattr__(self, "realtime_url", str(self.realtime_url).rstrip("/"))
        object.__setattr__(self, "default_headers", dict(self.default_headers))
        object.__setattr__(self, "default_properties", dict(self.default_properties))
        # Built directly (not via create()): switches come from code only.
        if not self.services_off:
            all_on, off = _services.resolve(self.services, self.enabled, {})
            object.__setattr__(self, "enabled", all_on)
            object.__setattr__(self, "services_off", off)
        else:
            object.__setattr__(self, "services_off", dict(self.services_off))

    def service_enabled(self, service: str) -> bool:
        """Whether a product is switched on (code + ``NEXUS_<SERVICE>_ENABLED``, off wins)."""
        return service not in self.services_off

    # ── derived ──────────────────────────────────────────────────────────────

    @property
    def http_base(self) -> str:
        """The API origin without a trailing slash."""
        return self.base_url

    @property
    def socket_base(self) -> str:
        """The Socket.IO origin (``realtime_url`` or the API origin)."""
        return self.realtime_url or self.base_url

    @property
    def resolved_user_agent(self) -> str:
        return self.user_agent or "inverge-nexus-python/{0}".format(__version__)

    def url_for(self, path: str) -> str:
        return self.base_url + (path if path.startswith("/") else "/" + path)

    def with_(self, **changes: Any) -> "NexusConfig":
        """A copy with some fields changed."""
        if "services" in changes or "enabled" in changes:
            # New switches: recompute against the environment (off wins).
            services = changes.get("services", self.services)
            enabled = changes.get("enabled", True)
            all_on, off = _services.resolve(services, enabled, os.environ)
            changes = {**changes, "services": dict(services or {}), "enabled": all_on, "services_off": off}
        return replace(self, **changes)

    # ── factories ────────────────────────────────────────────────────────────

    @classmethod
    def from_mapping(cls, options: Mapping[str, Any], api_key: Optional[str] = None) -> "NexusConfig":
        """Build from a plain dict.

        Keys may be snake_case (``base_url``) or the camelCase spellings used by
        the other Nexus SDKs (``baseUrl``) — both are accepted, so a shared
        config blob can drive any of them. Unknown keys are ignored.
        """

        def pick(*names: str) -> Any:
            for name in names:
                if name in options and options[name] is not None:
                    return options[name]
            return None

        key = api_key or pick("api_key", "apiKey", "API_KEY", "key")
        if key is None:
            raise ConfigurationError("A Nexus API key is required (nxs_...).")

        return cls(
            api_key=str(key),
            base_url=str(pick("base_url", "baseUrl", "BASE_URL") or DEFAULT_BASE_URL),
            realtime_url=(lambda v: str(v) if v else None)(pick("realtime_url", "realtimeUrl")),
            timeout=_as_float(pick("timeout", "TIMEOUT"), 10.0),
            max_retries=_as_int(pick("max_retries", "maxRetries"), 2),
            retry_backoff=_as_float(pick("retry_backoff", "retryBackoff"), 0.25),
            default_headers=dict(pick("default_headers", "defaultHeaders", "headers") or {}),
            app_version=(lambda v: str(v) if v else None)(pick("app_version", "appVersion")),
            release=(lambda v: str(v) if v else None)(pick("release")),
            os_type=(lambda v: str(v) if v else None)(pick("os_type", "osType")),
            os_version=(lambda v: str(v) if v else None)(pick("os_version", "osVersion")),
            default_properties=dict(pick("default_properties", "defaultProperties") or {}),
            batch=_as_bool(pick("batch", "BATCH"), True),
            flush_interval=_as_float(pick("flush_interval", "flushInterval"), 5.0),
            max_batch=_as_int(pick("max_batch", "maxBatch"), 50),
            max_queue=_as_int(pick("max_queue", "maxQueue"), 10000),
            silent=_as_bool(pick("silent", "SILENT"), False),
            debug=_as_bool(pick("debug", "DEBUG"), False),
            user_agent=(lambda v: str(v) if v else None)(pick("user_agent", "userAgent")),
            services=dict(pick("services") or {}),
            enabled=_services.is_on(pick("enabled")),
            services_off=dict(pick("services_off") or {}),
        )

    #: Config field → the environment variable that supplies it.
    ENV_VARS = {
        "api_key": "NEXUS_API_KEY",
        "base_url": "NEXUS_BASE_URL",
        "realtime_url": "NEXUS_REALTIME_URL",
        "timeout": "NEXUS_TIMEOUT",
        "max_retries": "NEXUS_MAX_RETRIES",
        "app_version": "NEXUS_APP_VERSION",
        "release": "NEXUS_RELEASE",
        "batch": "NEXUS_BATCH",
        "flush_interval": "NEXUS_FLUSH_INTERVAL",
        "max_batch": "NEXUS_MAX_BATCH",
        "silent": "NEXUS_SILENT",
        "debug": "NEXUS_DEBUG",
    }

    @classmethod
    def create(
        cls,
        api_key: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
        **options: Any,
    ) -> "NexusConfig":
        """Explicit options layered over the ``NEXUS_*`` environment.

        This is what the clients use: settings you don't pass in code (a
        timeout, retries, batching) can still come from the environment.
        Precedence, lowest first: environment → ``options`` → ``api_key``.
        """
        env = os.environ if environ is None else environ
        values: Dict[str, Any] = {}
        for field_name, var in cls.ENV_VARS.items():
            value = env.get(var)
            if value is not None:
                values[field_name] = value
        values.update({k: v for k, v in options.items() if v is not None})
        if api_key:
            values["api_key"] = api_key
        # Service switches: code + NEXUS_<SERVICE>_ENABLED / NEXUS_ENABLED, off wins.
        all_on, off = _services.resolve(values.get("services"), values.get("enabled", True), env)
        values["enabled"] = all_on
        values["services_off"] = off
        if not values.get("api_key"):
            raise ConfigurationError(
                "No Nexus API key. Pass api_key='nxs_...' or export NEXUS_API_KEY."
            )
        return cls.from_mapping(values)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None, **overrides: Any) -> "NexusConfig":
        """Build purely from ``NEXUS_*`` environment variables.

        Recognised: ``NEXUS_API_KEY`` (required), ``NEXUS_BASE_URL``,
        ``NEXUS_REALTIME_URL``, ``NEXUS_TIMEOUT``, ``NEXUS_MAX_RETRIES``,
        ``NEXUS_APP_VERSION``, ``NEXUS_RELEASE``, ``NEXUS_BATCH``,
        ``NEXUS_FLUSH_INTERVAL``, ``NEXUS_MAX_BATCH``, ``NEXUS_SILENT``,
        ``NEXUS_DEBUG``. Keyword overrides win over the environment.
        """
        return cls.create(environ=environ, **overrides)
