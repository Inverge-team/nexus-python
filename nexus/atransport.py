"""Pluggable asynchronous HTTP transports (used by :class:`nexus.AsyncNexusClient`).

Picks the best available: ``httpx`` → ``aiohttp`` → a thread-pool wrapper around
the synchronous transport. The fallback still never blocks the event loop; it
just doesn't share an async connection pool.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional

from ._logging import logger
from .exceptions import TransportError
from .transport import RawResponse, Transport, UrllibTransport

__all__ = [
    "AsyncTransport",
    "HttpxAsyncTransport",
    "AiohttpTransport",
    "ThreadedAsyncTransport",
    "default_async_transport",
]


class AsyncTransport:
    """Interface for sending one HTTP request from async code."""

    async def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> RawResponse:  # pragma: no cover - interface
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release pooled connections. Safe to call more than once."""


class HttpxAsyncTransport(AsyncTransport):
    """Transport backed by a pooled ``httpx.AsyncClient``."""

    def __init__(self, client: Any = None) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise TransportError("HttpxAsyncTransport needs the `httpx` package installed.") from exc
        import httpx

        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> RawResponse:
        import httpx

        try:
            response = await self._client.request(
                method.upper(), url, headers=dict(headers), content=body, timeout=timeout
            )
        except httpx.HTTPError as exc:
            raise TransportError("{0} {1} failed: {2}".format(method.upper(), url, exc)) from exc
        return RawResponse(
            status=int(response.status_code),
            body=response.content or b"",
            headers={k.lower(): v for k, v in response.headers.items()},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            try:
                await self._client.aclose()
            except Exception:  # pragma: no cover - defensive
                pass


class AiohttpTransport(AsyncTransport):
    """Transport backed by an ``aiohttp.ClientSession``.

    The session is created lazily on first use so the transport can be built
    outside a running event loop (aiohttp binds a session to its loop).
    """

    def __init__(self, session: Any = None) -> None:
        try:
            import aiohttp  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise TransportError("AiohttpTransport needs the `aiohttp` package installed.") from exc
        self._session = session
        self._owns_session = session is None

    def _ensure_session(self) -> Any:
        if self._session is None:
            import aiohttp

            self._session = aiohttp.ClientSession()
        return self._session

    async def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> RawResponse:
        import aiohttp

        session = self._ensure_session()
        try:
            async with session.request(
                method.upper(),
                url,
                headers=dict(headers),
                data=body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                payload = await response.read()
                return RawResponse(
                    status=int(response.status),
                    body=payload or b"",
                    headers={k.lower(): v for k, v in response.headers.items()},
                )
        except asyncio.TimeoutError as exc:
            raise TransportError("{0} {1} timed out".format(method.upper(), url)) from exc
        except aiohttp.ClientError as exc:
            raise TransportError("{0} {1} failed: {2}".format(method.upper(), url, exc)) from exc

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None:
            try:
                await self._session.close()
            except Exception:  # pragma: no cover - defensive
                pass


class ThreadedAsyncTransport(AsyncTransport):
    """Runs a synchronous :class:`~nexus.transport.Transport` in a worker thread.

    The stdlib fallback: no third-party async client needed, and the event loop
    is never blocked.
    """

    def __init__(self, inner: Optional[Transport] = None) -> None:
        self._inner = inner or UrllibTransport()

    async def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> RawResponse:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._inner.send(method, url, headers, body, timeout)
        )

    async def aclose(self) -> None:
        self._inner.close()


def default_async_transport() -> AsyncTransport:
    """The best async transport available: httpx → aiohttp → threaded urllib."""
    for factory in (HttpxAsyncTransport, AiohttpTransport):
        try:
            return factory()
        except TransportError:
            continue
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("async transport %s unavailable: %s", factory.__name__, exc)
            continue
    return ThreadedAsyncTransport()
