"""Test helpers: a transport that records requests and replays canned responses."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from nexus.transport import RawResponse, Transport


class FakeTransport(Transport):
    """Records every request; answers from a queue of canned responses."""

    def __init__(self, responses: Optional[List[Any]] = None) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.responses: List[Any] = list(responses or [])
        self.default = RawResponse(200, b"{}")

    def queue(self, payload: Any, status: int = 200) -> "FakeTransport":
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.responses.append(RawResponse(status, body))
        return self

    def queue_error(self, status: int, code: str = "bad_request", message: str = "nope") -> "FakeTransport":
        return self.queue({"error": {"code": code, "message": message}}, status)

    def queue_exception(self, exc: BaseException) -> "FakeTransport":
        self.responses.append(exc)
        return self

    def send(self, method, url, headers, body, timeout):  # type: ignore[no-untyped-def]
        self.requests.append(
            {
                "method": method,
                "url": url,
                "path": url.split("://", 1)[-1].split("/", 1)[-1],
                "headers": dict(headers),
                "json": json.loads(body.decode()) if body else None,
                "timeout": timeout,
            }
        )
        if self.responses:
            nxt = self.responses.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            return nxt
        return self.default

    # ── assertions ───────────────────────────────────────────────────────────

    @property
    def last(self) -> Dict[str, Any]:
        assert self.requests, "no request was sent"
        return self.requests[-1]

    def paths(self) -> List[str]:
        return ["/" + r["path"] for r in self.requests]


class FakeAsyncTransport:
    """Async twin of :class:`FakeTransport`."""

    def __init__(self) -> None:
        self.inner = FakeTransport()

    async def send(self, method, url, headers, body, timeout):  # type: ignore[no-untyped-def]
        return self.inner.send(method, url, headers, body, timeout)

    async def aclose(self) -> None:
        return None

    def queue(self, payload: Any, status: int = 200) -> "FakeAsyncTransport":
        self.inner.queue(payload, status)
        return self

    @property
    def requests(self):  # type: ignore[no-untyped-def]
        return self.inner.requests

    @property
    def last(self):  # type: ignore[no-untyped-def]
        return self.inner.last


def make_client(**options: Any):
    """A client wired to a :class:`FakeTransport`, batching off."""
    from nexus import NexusClient

    transport = FakeTransport()
    options.setdefault("base_url", "https://api.test")
    options.setdefault("batch", False)
    options.setdefault("max_retries", 0)
    client = NexusClient("nxs_test_secret", transport=transport, **options)
    return client, transport
