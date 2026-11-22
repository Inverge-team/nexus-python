"""The billing-suspension circuit breaker, shared by every client in the process.

When the API answers 402 ``billing_suspended`` (an overdue invoice), the SDK
stops calling it for the pause the API asks for (``details.retryAfterSeconds``,
default 5 minutes): calls made meanwhile raise
:class:`~nexus.exceptions.BillingSuspendedError` locally, with no request —
and telemetry (events, logs, errors, Telescope) is dropped quietly. When the
pause ends ONE request goes out as a probe (it holds a short lease so other
threads keep waiting): a 402 starts a new pause, any other answer means access
is back.

Without this a suspended app keeps calling at full rate — and one that reports
its own failures to Nexus Errors (a Celery task failure, a log handler) turns
every refused call into another one.

Messaging is exempt: it is paid from its own prepaid wallet and the API never
suspends it. Its 402 ``insufficient_balance`` (an empty wallet) is not a
suspension.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Dict, Optional

DEFAULT_PAUSE_SECONDS = 300
_MIN_SECONDS = 30
_MAX_SECONDS = 3600
_PROBE_LEASE = 30.0

_EXEMPT = re.compile(r"^/partner/messaging(?:[/?]|$)")

_lock = threading.Lock()
_pauses: Dict[str, float] = {}  # key -> pause end (time.time())


def key_for(base_url: str, api_key: str) -> str:
    """Identifies one API key on one base URL without keeping the key itself."""
    return hashlib.sha256("{0}|{1}".format(base_url.rstrip("/"), api_key).encode("utf-8")).hexdigest()[:32]


def is_exempt(path: str) -> bool:
    """Whether ``path`` is billed from a prepaid wallet and so never suspended."""
    return bool(_EXEMPT.match(path))


#: Fire-and-forget services: while suspended their calls return None instead of raising.
_TELEMETRY = frozenset({"events", "errors", "logs", "telescope", "replay"})


def is_telemetry(path: str) -> bool:
    from ._services import service_for_path

    return service_for_path(path) in _TELEMETRY


def is_suspension(status: int, code: Any) -> bool:
    """Whether an API answer is the billing-suspension refusal."""
    return status == 402 and code == "billing_suspended"


def remaining(key: str, path: str = "") -> float:
    """Seconds the pause still has to run (0 = not paused). Read-only."""
    if path and is_exempt(path):
        return 0.0
    with _lock:
        until = _pauses.get(key)
    return 0.0 if until is None else max(0.0, until - time.time())


def begin(key: str, path: str) -> float:
    """Before a request: 0 to send it, else the seconds left to refuse it with.

    When a pause has just ended, the caller becomes the probe.
    """
    if is_exempt(path):
        return 0.0
    with _lock:
        until = _pauses.get(key)
        if until is None:
            return 0.0
        now = time.time()
        if until > now:
            return until - now
        _pauses[key] = now + _PROBE_LEASE
        return 0.0


def record(key: str, path: str, status: int, code: Any = None, details: Any = None) -> Optional[int]:
    """After an HTTP answer: start/extend the pause on a suspension (returns its
    length), lift it on any other answer."""
    if is_suspension(status, code):
        raw = details.get("retryAfterSeconds") if isinstance(details, dict) else None
        seconds = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else DEFAULT_PAUSE_SECONDS
        seconds = min(_MAX_SECONDS, max(_MIN_SECONDS, seconds))
        with _lock:
            _pauses[key] = time.time() + seconds
        return seconds
    if not is_exempt(path):
        with _lock:
            _pauses.pop(key, None)
    return None


def reset() -> None:
    """Forget every pause (tests; or after settling an invoice, to retry at once)."""
    with _lock:
        _pauses.clear()


def is_suspension_report(path: str, json: Any) -> bool:
    """An error report whose subject is the API's own billing refusal (queued
    by an older SDK). Sending it can only be refused again, or flood Errors."""
    if path != "/partner/errors" or not isinstance(json, dict):
        return False
    return str(json.get("message") or "").startswith("API access is suspended")


def local_message(seconds: float) -> str:
    return (
        "API access is suspended for an overdue invoice. Nexus calls are paused for "
        "{0} more seconds; settle the outstanding balance to restore access.".format(max(1, int(seconds + 0.999)))
    )
