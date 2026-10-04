"""Error monitoring — report exceptions and error messages from your backend."""

from __future__ import annotations

import traceback
from types import TracebackType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from .. import _payloads as p
from .base import Resource

__all__ = ["Errors", "stack_frames", "exception_fingerprint"]

ExcInfo = Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]]


def stack_frames(
    exc: Optional[BaseException] = None,
    tb: Optional[TracebackType] = None,
    *,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Normalised ``{function, filename, lineno}`` frames the console renders.

    Frames come back **throw-site first** (the reverse of
    :func:`traceback.extract_tb`), matching the other Nexus SDKs so the first
    line in the console is where it broke.
    """
    traceback_obj = tb or (exc.__traceback__ if exc is not None else None)
    if traceback_obj is None:
        return []
    frames: List[Dict[str, Any]] = []
    for frame in traceback.extract_tb(traceback_obj, limit=limit):
        entry: Dict[str, Any] = {
            "function": frame.name,
            "filename": frame.filename,
            "lineno": frame.lineno,
        }
        if getattr(frame, "line", None):
            entry["context_line"] = frame.line
        frames.append(entry)
    frames.reverse()
    return frames


def exception_fingerprint(exc: BaseException) -> str:
    """A stable group key: exception type + the deepest frame that raised it."""
    frames = stack_frames(exc, limit=1)
    where = ""
    if frames:
        where = "{0}:{1}".format(frames[0].get("filename") or "?", frames[0].get("lineno") or 0)
    return "{0}@{1}".format(type(exc).__name__, where) if where else type(exc).__name__


class Errors(Resource):
    """``client.errors``"""

    def capture(
        self,
        message: str,
        *,
        type: Optional[str] = None,  # noqa: A002 - mirrors the API field name
        level: Optional[str] = None,
        handled: Optional[bool] = None,
        fingerprint: Optional[str] = None,
        stack: Optional[Union[str, Sequence[Mapping[str, Any]]]] = None,
        context: Optional[Mapping[str, Any]] = None,
        release: Optional[str] = None,
        url: Optional[str] = None,
        distinct_id: Optional[str] = None,
        session_key: Optional[str] = None,
        device_key: Optional[str] = None,
        os_type: Optional[str] = None,
        os_version: Optional[str] = None,
        browser: Optional[str] = None,
        app_version: Optional[str] = None,
        **extra_context: Any,
    ) -> Dict[str, Any]:
        """Report an error by message.

        ::

            client.errors.capture("Payment gateway timeout",
                                  type="GatewayTimeout", fingerprint="gateway-timeout")
        """
        merged_context: Dict[str, Any] = {}
        if context:
            merged_context.update(context)
        if extra_context:
            merged_context.update(extra_context)

        fields = self._context(
            distinct_id=distinct_id,
            session_key=session_key,
            device_key=device_key,
            release=release,
            app_version=app_version,
            os_type=os_type,
            os_version=os_version,
            browser=browser,
            url=url,
        )
        fields.update(
            {
                "message": message,
                "type": type,
                "level": level,
                "handled": handled,
                "fingerprint": fingerprint,
                "stack": list(stack) if isinstance(stack, (list, tuple)) else stack,
                "context": merged_context or None,
            }
        )
        return self._dict(self._send(p.errors_capture(fields)))

    def capture_exception(
        self,
        exc: Optional[BaseException] = None,
        *,
        handled: bool = True,
        level: Optional[str] = "error",
        fingerprint: Optional[str] = None,
        include_cause: bool = True,
        **options: Any,
    ) -> Dict[str, Any]:
        """Report a caught exception, with its type, message and stack.

        Pass the exception, or call it bare inside an ``except`` block and the
        SDK reads :func:`sys.exc_info`::

            try:
                charge(order)
            except PaymentError:
                client.errors.capture_exception(context={"order": order.id})
        """
        if exc is None:
            import sys

            exc = sys.exc_info()[1]
        if exc is None:
            return self.capture("capture_exception() called with no active exception", level="warn")

        message = str(exc) or type(exc).__name__
        frames = stack_frames(exc)

        if include_cause:
            cause = exc.__cause__ or exc.__context__
            depth = 0
            while cause is not None and depth < 3:
                frames.append(
                    {
                        "function": "caused by {0}: {1}".format(type(cause).__name__, cause),
                        "filename": None,
                        "lineno": None,
                    }
                )
                frames.extend(stack_frames(cause))
                cause = cause.__cause__ or cause.__context__
                depth += 1

        options.setdefault("type", type(exc).__name__)
        return self.capture(
            message,
            level=level,
            handled=handled,
            fingerprint=fingerprint,
            stack=frames or None,
            **options
        )

    def capture_exc_info(self, exc_info: ExcInfo, **options: Any) -> Dict[str, Any]:
        """Report from a :func:`sys.exc_info` triple (handy in log handlers)."""
        exc = exc_info[1] if exc_info else None
        if exc is None:
            return {}
        return self.capture_exception(exc, **options)
