"""The process-wide default client — the "just import nexus and go" path.

Scripts, Celery tasks, management commands and small services rarely want to
thread a client object through every layer. :func:`init` builds one and stores
it; everything else in the package finds it from there::

    import nexus

    nexus.init("nxs_live_xxx")             # or NEXUS_API_KEY in the environment
    nexus.identify("user_123")
    nexus.track("report_generated", {"rows": 1200})
    nexus.events.track("another_one")      # resources work too
    nexus.flush()

The framework integrations call :func:`init` for you.
"""

from __future__ import annotations

from typing import Any, Optional

from ._logging import logger
from .client import NexusClient
from .config import NexusConfig
from .exceptions import ConfigurationError

__all__ = [
    "init",
    "get_client",
    "set_client",
    "is_initialized",
    "shutdown",
    "identify",
    "reset",
    "track",
    "log",
    "capture_exception",
    "emit",
    "is_enabled",
    "flush",
    "resource",
]

_client: Optional[NexusClient] = None

#: Resource attributes reachable as ``nexus.<name>`` once initialised.
RESOURCE_NAMES = (
    "sessions",
    "events",
    "logs",
    "errors",
    "flags",
    "remote_config",
    "links",
    "realtime",
    "surveys",
    "push",
    "inapp",
    "live_activities",
    "replay",
    "voice",
)


def init(
    api_key: Optional[str] = None,
    *,
    config: Optional[NexusConfig] = None,
    capture_errors: bool = True,
    replace: bool = True,
    **options: Any,
) -> NexusClient:
    """Create (or replace) the default client.

    Args:
        api_key: The ``nxs_...`` key. Omit it to read ``NEXUS_API_KEY``.
        config: A prepared :class:`~nexus.NexusConfig`.
        capture_errors: Install hooks that report uncaught exceptions and flush
            at exit. Turn it off inside a framework whose integration already
            reports errors.
        replace: When ``False``, an existing default client is returned
            untouched (handy for idempotent app startup).
        **options: Any :class:`~nexus.NexusConfig` field.
    """
    global _client
    if _client is not None and not replace:
        return _client
    if _client is not None:
        try:
            _client.close(timeout=1.0)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("nexus: closing previous default client failed: %s", exc)

    client = NexusClient(api_key, config=config, **options)
    _client = client

    if capture_errors:
        from .excepthook import install_excepthook

        install_excepthook(client)
    return client


def get_client(required: bool = True) -> Optional[NexusClient]:
    """The default client.

    With ``required=True`` (the default) this raises when :func:`init` has not
    run; pass ``required=False`` to get ``None`` instead — useful in library
    code that should stay quiet when Nexus isn't configured.
    """
    if _client is None and required:
        raise ConfigurationError(
            "Nexus is not initialised. Call nexus.init('nxs_...') (or set NEXUS_API_KEY) first."
        )
    return _client


def set_client(client: Optional[NexusClient]) -> None:
    """Install an already-built client as the default."""
    global _client
    _client = client


def is_initialized() -> bool:
    return _client is not None


def shutdown(timeout: float = 3.0) -> None:
    """Flush and close the default client."""
    global _client
    if _client is None:
        return
    try:
        _client.close(timeout)
    finally:
        _client = None


def _required() -> NexusClient:
    client = get_client()
    if client is None:  # pragma: no cover - get_client raises first
        raise ConfigurationError("Nexus is not initialised.")
    return client


def resource(name: str) -> Any:
    """Resolve ``nexus.<name>`` to the default client's resource."""
    return getattr(_required(), name)


# ── convenience proxies ──────────────────────────────────────────────────────


def identify(distinct_id: str, **options: Any) -> Any:
    """:meth:`nexus.NexusClient.identify` on the default client."""
    return _required().identify(distinct_id, **options)


def reset() -> None:
    """Forget the current user and start a new session."""
    _required().reset()


def track(name: str, properties: Any = None, **options: Any) -> None:
    """Capture an event on the default client."""
    _required().events.track(name, properties, **options)


def log(level: str, message: str, **options: Any) -> None:
    """Write a log line on the default client."""
    _required().logs.log(level, message, **options)


def capture_exception(exc: Optional[BaseException] = None, **options: Any) -> Any:
    """Report an exception on the default client."""
    return _required().errors.capture_exception(exc, **options)


def emit(room: str, events: Any, payload: Any = None) -> Any:
    """Emit a realtime event on the default client."""
    return _required().realtime.emit(room, events, payload)


def is_enabled(key: str, distinct_id: Optional[str] = None, **options: Any) -> bool:
    """Evaluate a feature flag on the default client."""
    return _required().flags.is_enabled(key, distinct_id, **options)


def flush(timeout: Optional[float] = None) -> None:
    """Ship buffered telemetry on the default client (no-op if uninitialised)."""
    if _client is not None:
        _client.flush(timeout)
