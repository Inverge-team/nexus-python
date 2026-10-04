"""Automatic capture of crashes that nobody caught.

Covers plain scripts, workers and anything without a framework middleware::

    import nexus
    nexus.init("nxs_live_xxx")      # installs these by default

Installs hooks for the main thread (:data:`sys.excepthook`), other threads
(:func:`threading.excepthook`, 3.8+), unawaited asyncio exceptions, and a final
flush at exit.
"""

from __future__ import annotations

import atexit
import sys
import threading
from typing import Any, Optional

from ._logging import logger

__all__ = ["install_excepthook", "uninstall_excepthook"]

_installed = False
_previous_sys_hook: Optional[Any] = None
_previous_thread_hook: Optional[Any] = None


def install_excepthook(client: Any, *, flush_at_exit: bool = True) -> None:
    """Report uncaught exceptions to ``client``. Idempotent."""
    global _installed, _previous_sys_hook, _previous_thread_hook
    if _installed:
        return
    _installed = True

    _previous_sys_hook = sys.excepthook

    def hook(exc_type: Any, exc_value: BaseException, tb: Any) -> None:
        # Ctrl-C and SystemExit are intentional; don't file them as crashes.
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            try:
                client.errors.capture_exception(exc_value, handled=False, level="fatal")
                client.flush(timeout=3.0)
            except Exception as exc:  # pragma: no cover - last-ditch
                logger.warning("nexus: failed to report uncaught exception: %s", exc)
        if _previous_sys_hook is not None:
            _previous_sys_hook(exc_type, exc_value, tb)

    sys.excepthook = hook

    if hasattr(threading, "excepthook"):
        _previous_thread_hook = threading.excepthook

        def thread_hook(args: Any) -> None:
            exc_value = getattr(args, "exc_value", None)
            if exc_value is not None and not isinstance(exc_value, SystemExit):
                try:
                    client.errors.capture_exception(
                        exc_value,
                        handled=False,
                        level="fatal",
                        context={"thread": getattr(getattr(args, "thread", None), "name", None)},
                    )
                except Exception as exc:  # pragma: no cover - last-ditch
                    logger.warning("nexus: failed to report thread exception: %s", exc)
            if _previous_thread_hook is not None:
                _previous_thread_hook(args)

        threading.excepthook = thread_hook  # type: ignore[assignment]

    if flush_at_exit:
        atexit.register(_flush, client)


def uninstall_excepthook() -> None:
    """Restore the previous hooks."""
    global _installed, _previous_sys_hook, _previous_thread_hook
    if not _installed:
        return
    if _previous_sys_hook is not None:
        sys.excepthook = _previous_sys_hook
    if _previous_thread_hook is not None and hasattr(threading, "excepthook"):
        threading.excepthook = _previous_thread_hook  # type: ignore[assignment]
    _installed = False
    _previous_sys_hook = None
    _previous_thread_hook = None


def _flush(client: Any) -> None:  # pragma: no cover - interpreter teardown
    try:
        client.flush(timeout=3.0)
    except Exception:
        pass
