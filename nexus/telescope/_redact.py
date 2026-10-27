"""Redaction and small helpers shared by the Telescope recorder."""

from __future__ import annotations

import fnmatch
import re
from typing import Any, Iterable, Set

DEFAULT_REDACT_KEYS = (
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "x-csrf-token", "x-csrftoken",
    "x-xsrf-token", "token", "access_token", "refresh_token", "id_token", "api_key", "apikey", "client_secret",
    "private_key", "credit_card", "card_number", "cvv", "cvc", "ssn", "csrfmiddlewaretoken", "sessionid",
)
REDACTED = "********"


class Redactor:
    """Replaces secret values before anything leaves the process. Keys match
    case-insensitively; any key containing "password" or "secret" too. The
    Nexus server redacts again with the environment's own list."""

    def __init__(self, extra: Iterable[str] = ()) -> None:
        self.keys: Set[str] = {k.lower() for k in (*DEFAULT_REDACT_KEYS, *extra)}

    def redact(self, value: Any, depth: int = 0) -> Any:
        if depth > 10:
            return value
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                lk = str(k).lower()
                if lk in self.keys or "password" in lk or "secret" in lk:
                    out[k] = v if v in (None, "") else REDACTED
                else:
                    out[k] = self.redact(v, depth + 1)
            return out
        if isinstance(value, (list, tuple)):
            return [self.redact(v, depth + 1) for v in value]
        return value


_STATUS_RANGE = re.compile(r"^(\d{3})\s*-\s*(\d{3})$")


def status_match(pattern: str, status: str) -> bool:
    """``200`` (exact), ``2xx`` (class) or ``301-304`` (inclusive range) against a 3-digit status."""
    if not re.fullmatch(r"\d{3}", status):
        return False
    p = pattern.strip().lower()
    if re.fullmatch(r"\dxx", p):
        return p[0] == status[0]
    m = _STATUS_RANGE.match(p)
    if m:
        return int(m.group(1)) <= int(status) <= int(m.group(2))
    return p == status


def glob_match(glob: str, value: str) -> bool:
    return fnmatch.fnmatchcase(value.lower(), glob.lower())


_TP = re.compile(r"^\s*[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}\s*$", re.I)


def parse_traceparent(value: Any):
    """W3C ``traceparent`` → ``(trace_id, span_id)`` or ``None``."""
    if not isinstance(value, str):
        return None
    m = _TP.match(value)
    if not m or set(m.group(1)) == {"0"} or set(m.group(2)) == {"0"}:
        return None
    return m.group(1).lower(), m.group(2).lower()


def safe(value: Any, depth: int = 0) -> Any:
    """A JSON-safe, bounded copy of anything."""
    if depth > 6:
        return "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "…"
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return "[binary {0} bytes]".format(len(value))
        return safe(text, depth)
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 100:
                out["…"] = "{0} more".format(len(value) - 100)
                break
            out[str(k)] = safe(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out_list = [safe(v, depth + 1) for v in items[:100]]
        if len(items) > 100:
            out_list.append("… {0} more".format(len(items) - 100))
        return out_list
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # pragma: no cover
            pass
    pk = getattr(value, "pk", None)
    if pk is not None:
        return "{0}:{1}".format(type(value).__name__, pk)
    return "{0}".format(type(value).__name__) if not isinstance(value, (str, int)) else value
