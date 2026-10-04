"""Read Nexus options out of Django settings."""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["django_settings", "client_options", "tracker_options"]

_DEFAULTS: Dict[str, Any] = {
    "API_KEY": None,
    "BASE_URL": None,
    "REALTIME_URL": None,
    "TIMEOUT": None,
    "APP_VERSION": None,
    "RELEASE": None,
    "BATCH": None,
    "FLUSH_INTERVAL": None,
    "MAX_BATCH": None,
    "SILENT": None,
    "DEBUG": None,
    "TRACK_REQUESTS": True,
    "TRACK_SESSIONS": False,
    "CAPTURE_ERRORS": True,
    "EVENT_NAME": "http_request",
    "SAMPLE_RATE": 1.0,
    "IGNORE_PATHS": None,
    "LOGGING": False,
    "LOG_LEVEL": "INFO",
    "LOGGER_NAME": "",
    "AUTO_INIT": True,
}


def django_settings() -> Dict[str, Any]:
    """Merge ``settings.NEXUS`` with any flat ``NEXUS_*`` settings.

    The dict wins over the flat spelling when both are present.
    """
    from django.conf import settings

    values = dict(_DEFAULTS)
    for key in _DEFAULTS:
        flat = getattr(settings, "NEXUS_{0}".format(key), None)
        if flat is not None:
            values[key] = flat
    block = getattr(settings, "NEXUS", None)
    if isinstance(block, dict):
        for key, value in block.items():
            values[key.upper()] = value
    return values


def client_options(values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The subset that configures :class:`nexus.NexusConfig`."""
    values = values or django_settings()
    mapping = {
        "API_KEY": "api_key",
        "BASE_URL": "base_url",
        "REALTIME_URL": "realtime_url",
        "TIMEOUT": "timeout",
        "APP_VERSION": "app_version",
        "RELEASE": "release",
        "BATCH": "batch",
        "FLUSH_INTERVAL": "flush_interval",
        "MAX_BATCH": "max_batch",
        "SILENT": "silent",
        "DEBUG": "debug",
    }
    return {dest: values[src] for src, dest in mapping.items() if values.get(src) is not None}


def tracker_options(values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The subset that configures the request tracker."""
    values = values or django_settings()
    options: Dict[str, Any] = {
        "track_requests": bool(values.get("TRACK_REQUESTS", True)),
        "track_sessions": bool(values.get("TRACK_SESSIONS", False)),
        "capture_errors": bool(values.get("CAPTURE_ERRORS", True)),
        "event_name": values.get("EVENT_NAME", "http_request"),
        "sample_rate": float(values.get("SAMPLE_RATE", 1.0)),
    }
    if values.get("IGNORE_PATHS") is not None:
        options["ignore_paths"] = tuple(values["IGNORE_PATHS"])
    return options
