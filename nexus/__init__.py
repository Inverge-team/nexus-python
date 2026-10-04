"""Nexus — the official Python SDK for the Inverge Nexus platform.

One SDK for every Nexus product: **product analytics, structured logs, error
monitoring, sessions, feature flags, Remote Config, realtime rooms, deep-link
attribution, surveys, push notifications, in-app messages, Live Activities,
session replay and Voice (CPaaS)**.

Framework-agnostic by design. Use it three ways:

**1. A client object** (explicit, testable — good for libraries and services)::

    from nexus import NexusClient

    nexus = NexusClient("nxs_live_xxx")
    nexus.events.track("order_placed", {"total": 42})

**2. The module-level default** (scripts, workers, management commands)::

    import nexus

    nexus.init("nxs_live_xxx")          # or set NEXUS_API_KEY
    nexus.track("report_generated", {"rows": 1200})
    nexus.flush()

**3. A framework integration** (Django, Flask, FastAPI/Starlette, Celery, or any
WSGI/ASGI app) — see :mod:`nexus.integrations`::

    from nexus.integrations.flask import NexusFlask
    NexusFlask(app)

For asyncio, :class:`nexus.aio.AsyncNexusClient` mirrors the whole surface.

Docs: https://nexus.inverge.net
"""

from __future__ import annotations

from typing import Any

from . import context
from ._payloads import RoomMessage
from ._version import __version__
from .client import NexusClient
from .config import DEFAULT_BASE_URL, NexusConfig
from .dispatcher import BackgroundDispatcher, DirectDispatcher, Dispatcher
from .exceptions import (
    ApiError,
    AuthenticationError,
    ConfigurationError,
    NexusError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    TransportError,
    ValidationError,
)
from ._default import (
    RESOURCE_NAMES,
    capture_exception,
    emit,
    flush,
    get_client,
    identify,
    init,
    is_enabled,
    is_initialized,
    log,
    reset,
    resource,
    set_client,
    shutdown,
    track,
)
from .logging_handler import NexusLogHandler, install_logging
from .models import FlagEvaluation, RemoteConfigSnapshot, RemoteConfigValue
from .transport import HttpxTransport, RequestsTransport, Transport, UrllibTransport

__all__ = [
    "__version__",
    # client & config
    "NexusClient",
    "NexusConfig",
    "DEFAULT_BASE_URL",
    # module-level default client
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
    # context
    "context",
    # values
    "RoomMessage",
    "FlagEvaluation",
    "RemoteConfigSnapshot",
    "RemoteConfigValue",
    # logging
    "NexusLogHandler",
    "install_logging",
    # transports & dispatchers
    "Transport",
    "UrllibTransport",
    "RequestsTransport",
    "HttpxTransport",
    "Dispatcher",
    "DirectDispatcher",
    "BackgroundDispatcher",
    # optional / lazily imported
    "RealtimeSocket",
    "AsyncNexusClient",
    # exceptions
    "NexusError",
    "ConfigurationError",
    "TransportError",
    "ApiError",
    "AuthenticationError",
    "NotFoundError",
    "PermissionDeniedError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
]

_LAZY = {"RealtimeSocket": ".realtime_socket", "AsyncNexusClient": ".aio"}


def __getattr__(name: str) -> Any:
    """Resolve ``nexus.<resource>`` against the default client, and keep the
    optional pieces (``RealtimeSocket``, ``AsyncNexusClient``) out of import
    time so their dependencies stay optional.
    """
    if name in _LAZY:
        import importlib

        module = importlib.import_module(_LAZY[name], __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name in RESOURCE_NAMES:
        return resource(name)
    raise AttributeError("module 'nexus' has no attribute {0!r}".format(name))


def __dir__() -> Any:
    return sorted(set(__all__) | set(RESOURCE_NAMES) | set(_LAZY))
