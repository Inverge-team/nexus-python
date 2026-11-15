"""Push Authentication — approve sign-ins and transactions on the user's phone.

The phone (with the Nexus Flutter SDK) holds two hardware keys: one that only
Face ID / fingerprint / the device PIN unlocks, for approvals, and one for
silent approvals. Your server starts a challenge; the person matches the
number on your screen (or taps, or reads a code off the phone) — or the device
answers silently — and you get a signed receipt::

    enrollment = client.push_auth.create_enrollment("user_123")      # → token for the app
    c = client.push_auth.create_challenge(
        "user_123", "Sign in to Acme", method="number_match",
        context={"device": "Chrome on macOS", "ip": ip},
    )
    show_on_screen(c["requesterView"]["number"])
    done = client.push_auth.wait_for_challenge(c["id"])
    if done["status"] == "approved":
        receipt = client.push_auth.verify_receipt(done["receipt"])   # single-use

Use a SERVER-only API key granted Push Authentication server access (console →
API keys → the key → Push Authentication → Server access; off by default) — never the
key in your app. Without the grant calls raise with ``server_access_required``.
Errors raise (``NexusError`` subclasses, ``code`` from the server).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .. import _payloads as p
from .base import Resource

__all__ = ["PushAuth", "challenge_fields", "verify_webhook"]


def challenge_fields(
    identity: str,
    message: str,
    *,
    fields: Optional[Sequence[Mapping[str, str]]] = None,
    context: Optional[Mapping[str, str]] = None,
    method: Optional[str] = None,
    methods: Optional[Sequence[str]] = None,
    method_options: Optional[Mapping[str, Mapping[str, Any]]] = None,
    factor_id: Optional[str] = None,
    hidden_details: Optional[Mapping[str, Any]] = None,
    expires_in_seconds: Optional[int] = None,
    notification: Optional[Mapping[str, str]] = None,
    fallback_to: Optional[str] = None,
    ref: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """The create-challenge body, in the API's field names."""
    if not identity:
        raise ValueError("push_auth.create_challenge() requires an identity (your user id).")
    if not message:
        raise ValueError("push_auth.create_challenge() requires a message — it is what the user approves.")
    details: Dict[str, Any] = {"message": message}
    if fields:
        details["fields"] = [{"label": str(f["label"]), "value": str(f["value"])} for f in fields]
    if context:
        details["context"] = {k: str(v) for k, v in context.items() if v is not None}
    return {
        "identity": identity,
        "details": details,
        "method": method,
        "methods": list(methods) if methods else None,
        "methodOptions": {k: dict(v) for k, v in method_options.items()} if method_options else None,
        "factorId": factor_id,
        "hiddenDetails": dict(hidden_details) if hidden_details else None,
        "expiresInSeconds": expires_in_seconds,
        "notification": dict(notification) if notification else None,
        "fallback": {"to": fallback_to} if fallback_to else None,
        "ref": ref,
        "idempotencyKey": idempotency_key,
    }


def verify_webhook(body: bytes, signature: Optional[str], timestamp: Any, nonce: Optional[str], secret: str, tolerance: int = 300) -> bool:
    """Check a Push Authentication webhook: HMAC-SHA256 over ``timestamp.nonce.body``.

    Pass the RAW body and the ``x-nexus-signature`` / ``x-nexus-timestamp`` /
    ``x-nexus-nonce`` headers. Dedupe on the body's ``id`` (at-least-once).
    """
    try:
        ts = int(str(timestamp))
    except (TypeError, ValueError):
        return False
    if not signature or not nonce or abs(time.time() - ts) > tolerance:
        return False
    raw = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), b"%d.%s." % (ts, nonce.encode("utf-8")) + bytes(raw), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(signature))


class PushAuth(Resource):
    """``client.push_auth``"""

    def _call(self, request: p.Request) -> Any:
        return self._client.request(request, direct=True)

    # enrollment & devices

    def create_enrollment(self, identity: str, *, friendly_name: Optional[str] = None, metadata: Optional[Mapping[str, Any]] = None, ttl_seconds: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """A single-use token (10 min) the app passes to ``Nexus.instance.pushAuth.enroll(token)``."""
        return self._call(p.push_auth_enrollment({"identity": identity, "friendlyName": friendly_name, "metadata": dict(metadata) if metadata else None, "ttlSeconds": ttl_seconds}))

    def list_factors(self, identity: Optional[str] = None, *, status: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self._call(p.push_auth_factors({"identity": identity, "status": status, "limit": limit})) or [])

    def get_factor(self, factor_id: str) -> Optional[Dict[str, Any]]:
        return self._call(p.push_auth_factor(factor_id))

    def revoke_factor(self, factor_id: str) -> Optional[Dict[str, Any]]:
        return self._call(p.push_auth_factor(factor_id, "revoke"))

    def lock_factor(self, factor_id: str) -> Optional[Dict[str, Any]]:
        return self._call(p.push_auth_factor(factor_id, "lock"))

    def unlock_factor(self, factor_id: str) -> Optional[Dict[str, Any]]:
        return self._call(p.push_auth_factor(factor_id, "unlock"))

    def get_identity(self, identity: str) -> Optional[Dict[str, Any]]:
        return self._call(p.push_auth_identity(identity))

    def unlock_identity(self, identity: str) -> Optional[Dict[str, Any]]:
        return self._call(p.push_auth_identity(identity, unlock=True))

    # challenges

    def create_challenge(self, identity: str, message: str, **options: Any) -> Optional[Dict[str, Any]]:
        """Start an approval. ``requesterView`` holds what your screen shows (e.g. the number)."""
        return self._call(p.push_auth_challenge_create(challenge_fields(identity, message, **options)))

    def get_challenge(self, challenge_id: str, *, wait: int = 0) -> Optional[Dict[str, Any]]:
        """Current state; ``wait`` (≤ 30 s) returns as soon as anything changes."""
        return self._call(p.push_auth_challenge_get(challenge_id, wait))

    def wait_for_challenge(self, challenge_id: str, *, timeout_seconds: float = 3600, until_stage: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Long-poll until decided (or until ``until_stage``, e.g. ``"awaiting_code"``)."""
        deadline = time.time() + timeout_seconds
        c = self.get_challenge(challenge_id)
        while c and c.get("status") == "pending" and (until_stage is None or c.get("stage") != until_stage):
            left = deadline - time.time()
            if left <= 0:
                break
            c = self.get_challenge(challenge_id, wait=int(min(25, max(1, left))))
        return c

    def complete(self, challenge_id: str, code: str) -> Optional[Dict[str, Any]]:
        """Submit the code the user typed (code display, or the SMS/Telegram fallback)."""
        return self._call(p.push_auth_challenge_complete(challenge_id, code))

    def cancel(self, challenge_id: str) -> Optional[Dict[str, Any]]:
        return self._call(p.push_auth_challenge_cancel(challenge_id))

    def verify_receipt(self, token: str, *, consume: bool = True) -> Optional[Dict[str, Any]]:
        """Verify an approval receipt; ``consume`` (default) makes it single-use."""
        return self._call(p.push_auth_verify_receipt(token, consume))
