"""HTTP execution: auth headers, JSON encoding, retries, error mapping.

The engine is the only place that knows how a :class:`~nexus._payloads.Request`
becomes bytes on a socket. Resources build requests; the engine runs them.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any, Dict, Optional

from ._logging import delivering, logger
from ._payloads import Request
from .atransport import AsyncTransport, default_async_transport
from .config import NexusConfig
from .exceptions import NexusError, TransportError, api_error_for
from .transport import RawResponse, Transport, default_transport

__all__ = ["HttpEngine", "AsyncHttpEngine"]

#: Statuses worth repeating for a request flagged as safe to retry.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _encode(payload: Optional[Dict[str, Any]]) -> Optional[bytes]:
    if payload is None:
        return None
    return json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")


class _EngineBase:
    def __init__(self, config: NexusConfig) -> None:
        self.config = config

    def _headers(self, request: Request, has_body: bool) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "x-api-key": self.config.api_key,
            "Accept": "application/json",
            "User-Agent": self.config.resolved_user_agent,
        }
        headers.update(self.config.default_headers)
        headers.update(request.headers)
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _attempts(self, request: Request) -> int:
        return 1 + max(0, self.config.max_retries) if request.retry else 1

    def _backoff(self, attempt: int, response: Optional[RawResponse]) -> float:
        if response is not None:
            raw = response.headers.get("retry-after")
            if raw:
                try:
                    return min(float(raw), 30.0)
                except ValueError:
                    pass
        base = self.config.retry_backoff * (2 ** attempt)
        return min(base + random.uniform(0, self.config.retry_backoff), 30.0)

    def _parse(self, request: Request, response: RawResponse) -> Any:
        if response.status == 304:
            # Remote Config conditional fetch — nothing changed.
            return {"notModified": True}
        if response.ok:
            return response.json()
        raise api_error_for(
            response.status,
            response.json(),
            method=request.method,
            path=request.path,
            raw=response.text[:2000],
            headers=response.headers,
        )

    def _should_retry(self, response: RawResponse) -> bool:
        # A usage cap set in the console is a 429 too, but waiting won't lift
        # it before next month — report it at once instead of backing off.
        if response.status == 429 and b"usage_limit_exceeded" in response.body:
            return False
        return response.status in RETRY_STATUSES


class HttpEngine(_EngineBase):
    """Synchronous request execution."""

    def __init__(self, config: NexusConfig, transport: Optional[Transport] = None) -> None:
        super().__init__(config)
        # An injected transport belongs to the caller; the default one is shared
        # process-wide (see transport.default_transport). Either way the engine
        # does not own it, so close() must not tear down its connection pool —
        # a second client in the same process would lose its transport.
        self.transport = transport or default_transport()

    def execute(self, request: Request) -> Any:
        # Mark the window so the SDK's own HTTP-client log records (httpx,
        # urllib3, …) are not fed back into Nexus logs. See nexus._logging.
        with delivering():
            return self._execute(request)

    def _execute(self, request: Request) -> Any:
        url = self.config.url_for(request.path)
        body = _encode(request.json)
        headers = self._headers(request, body is not None)
        attempts = self._attempts(request)
        last_error: Optional[NexusError] = None

        for attempt in range(attempts):
            if attempt:
                logger.debug("nexus: retry %d/%d %s %s", attempt, attempts - 1, request.method, request.path)
            try:
                response = self.transport.send(
                    request.method, url, headers, body, self.config.timeout
                )
            except TransportError as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise
                time.sleep(self._backoff(attempt, None))
                continue

            if not response.ok and self._should_retry(response) and attempt + 1 < attempts:
                time.sleep(self._backoff(attempt, response))
                continue
            return self._parse(request, response)

        raise last_error or TransportError("{0} {1} failed".format(request.method, url))

    def close(self) -> None:
        """No-op: the transport is shared or caller-owned.

        Use :func:`nexus.transport.close_default` at process shutdown if you
        really want the pooled connections closed.
        """


class AsyncHttpEngine(_EngineBase):
    """Asynchronous request execution."""

    def __init__(self, config: NexusConfig, transport: Optional[AsyncTransport] = None) -> None:
        super().__init__(config)
        # Async transports are built per client (not cached), so the engine owns
        # one it created and must close it; an injected one belongs to the caller.
        self._owns_transport = transport is None
        self.transport = transport or default_async_transport()

    async def execute(self, request: Request) -> Any:
        with delivering():
            return await self._execute(request)

    async def _execute(self, request: Request) -> Any:
        url = self.config.url_for(request.path)
        body = _encode(request.json)
        headers = self._headers(request, body is not None)
        attempts = self._attempts(request)
        last_error: Optional[NexusError] = None

        for attempt in range(attempts):
            try:
                response = await self.transport.send(
                    request.method, url, headers, body, self.config.timeout
                )
            except TransportError as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(self._backoff(attempt, None))
                continue

            if not response.ok and self._should_retry(response) and attempt + 1 < attempts:
                await asyncio.sleep(self._backoff(attempt, response))
                continue
            return self._parse(request, response)

        raise last_error or TransportError("{0} {1} failed".format(request.method, url))

    async def aclose(self) -> None:
        if self._owns_transport:
            await self.transport.aclose()
