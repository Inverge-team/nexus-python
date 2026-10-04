"""Forward Python :mod:`logging` records to Nexus.

Attach it once and every ``logger.info(...)`` already in your codebase shows up
in Nexus — no call-site changes::

    import logging, nexus

    client = nexus.init("nxs_live_xxx")
    logging.getLogger().addHandler(nexus.NexusLogHandler(client, level=logging.INFO))

Records carrying ``exc_info`` are *also* reported to error monitoring, so a
plain ``logger.exception("...")`` produces a grouped, stack-traced issue.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ._logging import in_delivery

__all__ = ["NexusLogHandler", "install_logging"]

#: Python level → Nexus level.
LEVEL_MAP = {
    logging.NOTSET: "trace",
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "fatal",
}

#: Attributes LogRecord always has — anything else the caller added via
#: ``extra={...}`` becomes Nexus log context.
_STANDARD = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "taskName"}


def nexus_level(levelno: int) -> str:
    """Map a numeric Python level onto a Nexus level name."""
    if levelno >= logging.CRITICAL:
        return "fatal"
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warn"
    if levelno >= logging.INFO:
        return "info"
    if levelno >= logging.DEBUG:
        return "debug"
    return "trace"


class NexusLogHandler(logging.Handler):
    """A :mod:`logging` handler that writes to the Nexus logs product."""

    def __init__(
        self,
        client: Any = None,
        level: int = logging.NOTSET,
        *,
        capture_errors: bool = True,
        include_extra: bool = True,
        source: Optional[str] = None,
    ) -> None:
        """
        Args:
            client: A :class:`nexus.NexusClient`. Defaults to the one from
                :func:`nexus.init`, resolved lazily on first record.
            level: Minimum level to forward.
            capture_errors: Also report records with ``exc_info`` as errors.
            include_extra: Send non-standard ``LogRecord`` attributes (anything
                passed as ``extra={...}``) as log context.
            source: Overrides the log's ``source``; defaults to the logger name.
        """
        super().__init__(level)
        self._client = client
        self.capture_errors = capture_errors
        self.include_extra = include_extra
        self.source = source

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        from ._default import get_client

        return get_client(required=False)

    def _extra(self, record: logging.LogRecord) -> Dict[str, Any]:
        if not self.include_extra:
            return {}
        return {
            key: value
            for key, value in vars(record).items()
            if key not in _STANDARD and not key.startswith("_")
        }

    def emit(self, record: logging.LogRecord) -> None:
        # Never feed the SDK's own diagnostics back into Nexus — that is how a
        # delivery failure turns into an infinite loop.
        if record.name == "nexus" or record.name.startswith("nexus."):
            return
        # Nor anything logged *by* a Nexus delivery. httpx logs every request at
        # INFO; without this, attaching the handler to the root logger makes the
        # SDK log its own traffic, forever.
        if in_delivery():
            return

        client = self._resolve_client()
        if client is None:
            return

        try:
            message = record.getMessage()
            context = self._extra(record)
            context.setdefault("logger", record.name)
            if record.funcName:
                context.setdefault("function", record.funcName)
            if record.pathname:
                context.setdefault("file", "{0}:{1}".format(record.pathname, record.lineno))

            client.logs.log(
                nexus_level(record.levelno),
                message,
                source=self.source or record.name,
                context=context,
            )

            if self.capture_errors and record.exc_info and record.exc_info[1] is not None:
                client.errors.capture_exception(
                    record.exc_info[1],
                    handled=True,
                    level="fatal" if record.levelno >= logging.CRITICAL else "error",
                    context=context,
                )
        except Exception:
            self.handleError(record)


def install_logging(
    client: Any = None,
    *,
    level: int = logging.INFO,
    logger_name: str = "",
    **options: Any,
) -> NexusLogHandler:
    """Attach a :class:`NexusLogHandler` to a logger (the root by default).

    Returns the handler so you can ``removeHandler`` it later.
    """
    handler = NexusLogHandler(client, level=level, **options)
    logging.getLogger(logger_name).addHandler(handler)
    return handler
