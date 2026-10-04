"""Pluggable synchronous HTTP transports.

The SDK ships three, and picks the best one installed:

* :class:`RequestsTransport` — ``requests``, connection pooling (preferred).
* :class:`HttpxTransport` — ``httpx``, HTTP/2 capable.
* :class:`UrllibTransport` — the stdlib fallback, so the SDK has **zero**
  required dependencies.

Bring your own by implementing :class:`Transport` and passing it to the client::

    NexusClient(config, transport=MyTransport())
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from ._logging import logger
from .exceptions import TransportError

__all__ = [
    "RawResponse",
    "Transport",
    "UrllibTransport",
    "RequestsTransport",
    "HttpxTransport",
    "default_transport",
    "close_default",
]


@dataclass
class RawResponse:
    """A transport-level HTTP response, before any Nexus semantics."""

    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        try:
            return self.body.decode("utf-8", "replace")
        except Exception:  # pragma: no cover - defensive
            return ""

    def json(self) -> Any:
        """Decode the body, or ``None`` when it is empty or not JSON."""
        text = self.text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None


class Transport:
    """Interface for sending one HTTP request."""

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> RawResponse:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        """Release pooled connections. Safe to call more than once."""


class UrllibTransport(Transport):
    """Zero-dependency transport on :mod:`urllib.request`.

    Opens a fresh connection per request, which is fine for low telemetry
    volume. Install ``requests`` or ``httpx`` to get connection pooling.
    """

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> RawResponse:
        request = urllib.request.Request(url=url, data=body, method=method.upper())
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return RawResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers={k.lower(): v for k, v in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            # A non-2xx is a normal outcome, not a transport failure — the
            # client turns it into an ApiError with the server's own message.
            payload = b""
            try:
                payload = exc.read()
            except Exception:  # pragma: no cover - defensive
                pass
            headers = {}
            if exc.headers is not None:
                headers = {k.lower(): v for k, v in exc.headers.items()}
            return RawResponse(status=int(exc.code), body=payload, headers=headers)
        except Exception as exc:  # URLError, socket.timeout, ssl errors, ...
            raise TransportError("{0} {1} failed: {2}".format(method.upper(), url, exc)) from exc


class RequestsTransport(Transport):
    """Transport backed by a pooled ``requests.Session``."""

    def __init__(self, session: Any = None) -> None:
        try:
            import requests  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise TransportError("RequestsTransport needs the `requests` package installed.") from exc
        import requests

        self._session = session or requests.Session()
        self._owns_session = session is None

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> RawResponse:
        import requests

        try:
            response = self._session.request(
                method.upper(), url, headers=dict(headers), data=body, timeout=timeout
            )
        except requests.RequestException as exc:
            raise TransportError("{0} {1} failed: {2}".format(method.upper(), url, exc)) from exc
        return RawResponse(
            status=int(response.status_code),
            body=response.content or b"",
            headers={k.lower(): v for k, v in response.headers.items()},
        )

    def close(self) -> None:
        if self._owns_session:
            try:
                self._session.close()
            except Exception:  # pragma: no cover - defensive
                pass


class HttpxTransport(Transport):
    """Transport backed by a pooled ``httpx.Client``."""

    def __init__(self, client: Any = None) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise TransportError("HttpxTransport needs the `httpx` package installed.") from exc
        import httpx

        self._client = client or httpx.Client()
        self._owns_client = client is None

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> RawResponse:
        import httpx

        try:
            response = self._client.request(
                method.upper(), url, headers=dict(headers), content=body, timeout=timeout
            )
        except httpx.HTTPError as exc:
            raise TransportError("{0} {1} failed: {2}".format(method.upper(), url, exc)) from exc
        return RawResponse(
            status=int(response.status_code),
            body=response.content or b"",
            headers={k.lower(): v for k, v in response.headers.items()},
        )

    def close(self) -> None:
        if self._owns_client:
            try:
                self._client.close()
            except Exception:  # pragma: no cover - defensive
                pass


_CACHED: Dict[str, Transport] = {}


def close_default() -> None:
    """Close the shared default transport and forget it.

    Rarely needed — the pool is meant to live as long as the process. Useful in
    a test suite that asserts no sockets are left open.
    """
    transport = _CACHED.pop("default", None)
    if transport is not None:
        transport.close()


def default_transport() -> Transport:
    """The process-wide default transport: requests → httpx → urllib.

    Cached, so every client in the process shares one connection pool. Because
    it is shared, no client closes it — see :func:`close_default`.
    """
    if "default" in _CACHED:
        return _CACHED["default"]
    for factory in (RequestsTransport, HttpxTransport):
        try:
            transport = factory()
        except TransportError:
            continue
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("transport %s unavailable: %s", factory.__name__, exc)
            continue
        _CACHED["default"] = transport
        return transport
    transport = UrllibTransport()
    _CACHED["default"] = transport
    return transport
