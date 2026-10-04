"""Structured logging — ``trace`` / ``debug`` / ``info`` / ``warn`` / ``error`` / ``fatal``.

For an app that already uses :mod:`logging`, prefer
:class:`nexus.NexusLogHandler`: attach it once and every ``logger.info(...)``
in your codebase lands in Nexus with no call-site changes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .. import _payloads as p
from .base import Resource

__all__ = ["Logs", "LEVELS"]

LEVELS = ("trace", "debug", "info", "warn", "error", "fatal")


class Logs(Resource):
    """``client.logs`` — buffered by default, like events."""

    def log(
        self,
        level: str,
        message: str,
        *,
        source: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[str] = None,
        distinct_id: Optional[str] = None,
        session_key: Optional[str] = None,
        device_key: Optional[str] = None,
        release: Optional[str] = None,
        app_version: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        browser: Optional[str] = None,
        **extra_context: Any,
    ) -> None:
        """Write one log line.

        ::

            client.logs.info("payment started", source="checkout", order_id=42)
        """
        merged: Dict[str, Any] = {}
        if context:
            merged.update(context)
        if extra_context:
            merged.update(extra_context)

        item = p.log_item(level, message, source, merged or None, timestamp)
        identity = self._context(
            distinct_id=distinct_id,
            session_key=session_key,
            device_key=device_key,
            release=release,
            app_version=app_version,
            os_type=os_type,
            os_version=os_version,
            browser=browser,
        )
        self._client.enqueue_log(item, identity)

    def trace(self, message: str, **options: Any) -> None:
        self.log("trace", message, **options)

    def debug(self, message: str, **options: Any) -> None:
        self.log("debug", message, **options)

    def info(self, message: str, **options: Any) -> None:
        self.log("info", message, **options)

    def warn(self, message: str, **options: Any) -> None:
        self.log("warn", message, **options)

    #: Alias so :mod:`logging` muscle memory works.
    warning = warn

    def error(self, message: str, **options: Any) -> None:
        self.log("error", message, **options)

    def fatal(self, message: str, **options: Any) -> None:
        self.log("fatal", message, **options)

    #: Alias so :mod:`logging` muscle memory works.
    critical = fatal

    def batch(
        self,
        logs: Iterable[Mapping[str, Any]],
        *,
        distinct_id: Optional[str] = None,
        session_key: Optional[str] = None,
        device_key: Optional[str] = None,
        release: Optional[str] = None,
        app_version: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        browser: Optional[str] = None,
    ) -> int:
        """Send many log lines **immediately**. Returns how many were written."""
        items: Sequence[Mapping[str, Any]] = [dict(line) for line in logs]
        if not items:
            return 0
        identity = self._context(
            distinct_id=distinct_id,
            session_key=session_key,
            device_key=device_key,
            release=release,
            app_version=app_version,
            os_type=os_type,
            os_version=os_version,
            browser=browser,
        )
        return self._written(self._send(p.logs_batch(items, identity)), len(items))

    def flush(self, timeout: Optional[float] = None) -> None:
        """Ship everything buffered right now."""
        self._client.flush_logs(timeout)
