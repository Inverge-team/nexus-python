"""Shield — verify that a request really comes from your app on a real device.

Your mobile/web app (with the Nexus client SDK and Shield on) sends two headers
with each request to YOUR backend: ``X-Nexus-Shield`` (a device-bound token) and
``DPoP`` (a single-use proof for that exact method + URL). Pass them here with
the request's method and URL; act on ``decision``::

    verdict = client.shield.verify_request(
        headers=request.headers, method=request.method,
        url=request.build_absolute_uri(), body=request.body, resource="checkout",
    )
    if not verdict.allowed:
        return HttpResponse(status=401)

Replay protection, blocked devices and the policy you configured for the
resource in the console are all applied server-side.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .. import _payloads as p
from .base import Resource

__all__ = ["Shield", "ShieldVerdict", "body_hash", "shield_fields"]


@dataclass(frozen=True)
class ShieldVerdict:
    """The outcome of a verification."""

    valid: bool
    decision: str
    mode: str
    reason: Optional[str]
    resource: str
    policy: Optional[str] = None
    score: Optional[int] = None
    level: Optional[str] = None
    providers: List[str] = field(default_factory=list)
    device_id: Optional[str] = None
    app_id: Optional[str] = None
    platform: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """True when your backend should serve the request."""
        return self.decision == "allow"

    @classmethod
    def from_response(cls, data: Optional[Mapping[str, Any]], resource: str) -> "ShieldVerdict":
        if not data:
            # Switched off / no answer: report it, let the caller choose fail-open or fail-closed.
            return cls(valid=False, decision="deny", mode="none", reason="unavailable", resource=resource)
        tok = data.get("token") or {}
        return cls(
            valid=bool(data.get("valid")),
            decision=str(data.get("decision", "deny")),
            mode=str(data.get("mode", "none")),
            reason=data.get("reason"),
            resource=str(data.get("resource", resource)),
            policy=data.get("policy"),
            score=tok.get("score"),
            level=tok.get("level"),
            providers=list(tok.get("providers") or []),
            device_id=tok.get("deviceId"),
            app_id=tok.get("appId"),
            platform=tok.get("platform"),
            raw=dict(data),
        )

    @classmethod
    def missing(cls, resource: str) -> "ShieldVerdict":
        return cls(valid=False, decision="deny", mode="none", reason="token_missing", resource=resource)


def body_hash(body: Optional[bytes]) -> Optional[str]:
    """base64url SHA-256 of a raw request body (what the client's ``bh`` claim holds)."""
    if body is None:
        return None
    if isinstance(body, str):
        body = body.encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode("ascii")


_body_hash = body_hash  # `verify(body_hash=…)` shadows the function name inside the class


def _header(headers: Mapping[str, Any], name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name:
            return value[0] if isinstance(value, (list, tuple)) else value
    return None


def shield_fields(
    token: Optional[str],
    proof: Optional[str],
    method: str,
    url: str,
    resource: Optional[str],
    body_hash_: Optional[str],
) -> Dict[str, Any]:
    if token and token.lower().startswith("dpop "):
        token = token[5:]
    return {"token": token, "proof": proof, "method": method.upper(), "url": url, "resource": resource, "bodyHash": body_hash_}


class Shield(Resource):
    """``client.shield``"""

    def verify(
        self,
        token: Optional[str],
        proof: Optional[str],
        method: str,
        url: str,
        resource: Optional[str] = None,
        body_hash: Optional[str] = None,
    ) -> ShieldVerdict:
        """Verify a token + proof. Use a SERVER-side API key."""
        res = resource or "*"
        if not token:
            return ShieldVerdict.missing(res)
        data = self._send(p.shield_verify(shield_fields(token, proof, method, url, resource, body_hash)))
        return ShieldVerdict.from_response(data, res)

    def verify_request(
        self,
        headers: Mapping[str, Any],
        method: str,
        url: str,
        body: Optional[bytes] = None,
        resource: Optional[str] = None,
    ) -> ShieldVerdict:
        """Verify from an incoming request's headers (any framework's mapping works)."""
        return self.verify(
            _header(headers, "x-nexus-shield"),
            _header(headers, "dpop"),
            method,
            url,
            resource,
            _body_hash(body),
        )
