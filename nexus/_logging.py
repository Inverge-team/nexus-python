"""The SDK's own diagnostic logger (not the Nexus *logs* product).

Everything the SDK says about itself goes to the ``nexus`` logger, so an app can
silence or route it like any other library logger::

    logging.getLogger("nexus").setLevel(logging.ERROR)
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from typing import Iterator

__all__ = ["logger", "set_debug", "delivering", "in_delivery"]

logger = logging.getLogger("nexus")
logger.addHandler(logging.NullHandler())


def set_debug(enabled: bool) -> None:
    """Turn verbose SDK logging on or off."""
    if enabled and logger.level in (logging.NOTSET, logging.WARNING):
        logger.setLevel(logging.DEBUG)


# ── delivery re-entrancy guard ───────────────────────────────────────────────
#
# The SDK's own HTTP client logs. `httpx` writes "HTTP Request: POST …" at INFO
# on every call; urllib3 and aiohttp do the same at DEBUG. If NexusLogHandler is
# attached to the root logger — the normal thing to do — those records become
# Nexus log lines, whose delivery logs again, forever.
#
# Rather than blanket-muting those libraries (which would also hide the app's
# own HTTP logs), the engine marks the window in which it is delivering, and the
# handler drops anything logged inside it. A ContextVar, not a thread-local, so
# it is correct for both worker threads and asyncio tasks.

_delivering: "contextvars.ContextVar[bool]" = contextvars.ContextVar("nexus_delivering", default=False)


def in_delivery() -> bool:
    """True while this thread/task is inside a Nexus HTTP request."""
    return _delivering.get()


@contextmanager
def delivering() -> Iterator[None]:
    """Mark the current thread/task as delivering to Nexus."""
    token = _delivering.set(True)
    try:
        yield
    finally:
        _delivering.reset(token)
