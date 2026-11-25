"""Every exception the SDK can raise.

All of them derive from :class:`NexusError`, so ``except NexusError`` is always
enough to contain the SDK::

    from nexus import NexusError

    try:
        nexus.push.send_to_user("u_1", title="Hi")
    except NexusError as exc:
        log.warning("nexus unavailable: %s", exc)
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

__all__ = [
    "NexusError",
    "ConfigurationError",
    "TransportError",
    "ApiError",
    "AuthenticationError",
    "PermissionDeniedError",
    "PermissionError_",
    "NotFoundError",
    "ValidationError",
    "RateLimitError",
    "ConflictError",
    "ServiceDisabledError",
    "ServerError",
    "BillingSuspendedError",
    "is_delivery_failure",
    "api_error_for",
]


class NexusError(Exception):
    """Base class for every Nexus SDK failure."""


class ConfigurationError(NexusError, ValueError):
    """The SDK was configured wrongly (missing API key, bad base URL, ...)."""


class TransportError(NexusError):
    """A network-level failure: DNS, connection refused, TLS, timeout.

    The original exception, when there was one, is available as ``__cause__``.
    """


class ApiError(NexusError):
    """A non-2xx response from the Nexus API.

    The API answers failures with ``{"error": {"code", "message", "details"}}``;
    those three fields are exposed as :attr:`code`, the exception message, and
    :attr:`details`.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: Optional[str] = None,
        details: Any = None,
        method: Optional[str] = None,
        path: Optional[str] = None,
        body: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.details = details
        self.method = method
        self.path = path
        self.body = body

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        where = " on {0} {1}".format(self.method, self.path) if self.path else ""
        code = " [{0}]".format(self.code) if self.code else ""
        return "Nexus API error {0}{1}{2}: {3}".format(self.status, code, where, self.message)


class AuthenticationError(ApiError):
    """401 — the API key is missing, malformed, or revoked."""


class PermissionDeniedError(ApiError):
    """403 — the key is valid but not allowed to do this."""


#: Alias kept for symmetry with the other SDKs' naming.
PermissionError_ = PermissionDeniedError


class NotFoundError(ApiError):
    """404 — no such resource in this environment."""


class ValidationError(ApiError):
    """400/422 — the request body failed server-side validation."""


class ConflictError(ApiError):
    """409 — the write conflicts with the current state: a DocStore version
    precondition failed (:attr:`is_precondition_failed`), a unique constraint
    was violated, or a document already exists."""

    @property
    def is_precondition_failed(self) -> bool:
        return isinstance(self.details, Mapping) and self.details.get("reason") == "precondition_failed"


class ServiceDisabledError(NexusError):
    """The product is switched off (``NEXUS_<SERVICE>_ENABLED=false`` / ``services=``).

    Most services turn into silent no-ops when switched off; DocStore raises
    this instead, because a database read that quietly returns nothing looks
    exactly like missing data.
    """


class RateLimitError(ApiError):
    """429 — too many requests. :attr:`retry_after` is in seconds when sent."""

    def __init__(self, *args: Any, retry_after: Optional[float] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class BillingSuspendedError(ApiError):
    """402 ``billing_suspended`` — the organization's API access is suspended
    for an overdue invoice.

    After the first one the SDK stops calling the API for :attr:`retry_after`
    seconds; calls made meanwhile raise this at once with :attr:`local` true and
    no request is sent (telemetry is dropped quietly). Access returns by itself
    once the invoice is settled.
    """

    def __init__(self, *args: Any, retry_after: float = 0, local: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after
        self.local = local


def is_delivery_failure(exc: Optional[BaseException]) -> bool:
    """Whether ``exc`` (or what it was raised from) is the SDK failing to REACH
    Nexus — billing suspension, rate limiting, an outage, the network.

    Those are never reported to Nexus Errors: the report would travel the same
    failing path, fail, and be reported again. A 4xx the API answered for a bad
    request (validation, not found…) is still the app's bug and is reported.
    """
    seen = 0
    while exc is not None and seen < 10:
        if isinstance(exc, (BillingSuspendedError, TransportError)):
            return True
        if isinstance(exc, ApiError) and (exc.code == "billing_suspended" or exc.status == 429 or exc.status >= 500):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


class ServerError(ApiError):
    """5xx — the Nexus API failed. Safe to retry idempotent calls."""


_STATUS_MAP = {
    400: ValidationError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
    429: RateLimitError,
}


def api_error_for(
    status: int,
    payload: Any,
    *,
    method: Optional[str] = None,
    path: Optional[str] = None,
    raw: Optional[str] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> ApiError:
    """Build the most specific :class:`ApiError` subclass for a response."""
    error: Mapping[str, Any] = {}
    if isinstance(payload, Mapping):
        nested = payload.get("error")
        error = nested if isinstance(nested, Mapping) else payload

    message = error.get("message") or error.get("error")
    if not isinstance(message, str) or not message:
        message = "Nexus API request failed with status {0}".format(status)

    code = error.get("code")
    details = error.get("details")

    cls = _STATUS_MAP.get(status) or (ServerError if status >= 500 else ApiError)
    kwargs = dict(
        status=status,
        code=code if isinstance(code, str) else None,
        details=details,
        method=method,
        path=path,
        body=raw,
    )
    if status == 402 and code == "billing_suspended":
        from . import _suspension

        raw_seconds = details.get("retryAfterSeconds") if isinstance(details, Mapping) else None
        return BillingSuspendedError(
            message,
            retry_after=float(raw_seconds) if isinstance(raw_seconds, (int, float)) else float(_suspension.DEFAULT_PAUSE_SECONDS),
            **kwargs,  # type: ignore[arg-type]
        )
    if cls is RateLimitError:
        retry_after = None
        if headers:
            raw_retry = headers.get("retry-after") or headers.get("Retry-After")
            try:
                retry_after = float(raw_retry) if raw_retry is not None else None
            except (TypeError, ValueError):
                retry_after = None
        return RateLimitError(message, retry_after=retry_after, **kwargs)  # type: ignore[arg-type]
    return cls(message, **kwargs)  # type: ignore[arg-type]
