"""The END USER's IP for the request being handled, for Messaging's per-IP limit.

The messaging API is server-to-server: the address Nexus sees is your server's,
the same for every request. So the SDK forwards your end user's address as
``X-Client-IP`` — on ``verifications.start()`` and on
``send(message_class="otp")``. Other sends never carry it by themselves: a
message triggered by a webhook would otherwise be counted against the webhook
sender's address.

Where it comes from:

- The Nexus Django / Flask / ASGI / WSGI middleware binds it per request, from
  the address the framework resolved (``REMOTE_ADDR``, ``request.remote_addr``,
  the ASGI ``client``) — never from a raw ``X-Forwarded-For``, which a client
  can write itself. Behind a proxy, have the framework or server resolve the
  real address (Werkzeug ``ProxyFix``, ``uvicorn --proxy-headers
  --forwarded-allow-ips``, gunicorn ``forwarded_allow_ips``, a Django
  middleware that sets ``REMOTE_ADDR``).
- Celery: :func:`nexus.integrations.celery.propagate_client_ip` (also run by
  ``install_celery``) puts it in the task headers when the task is queued and
  binds it again on the worker.
- Anything else (RQ, Dramatiq, threads): pass :func:`current_client_ip` along
  with the job and wrap the work in :func:`client_ip`.

Private, loopback and link-local addresses are never forwarded: one of those is
your proxy or load balancer, and counting every user against it would
rate-limit them all together.
"""

from __future__ import annotations

import contextvars
import ipaddress
import os
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

__all__ = [
    "client_ip",
    "bind_client_ip",
    "unbind_client_ip",
    "current_client_ip",
    "public_ip",
    "set_forwarding",
    "client_ip_headers",
]

_var: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("nexus_client_ip", default=None)
_forwarding: Optional[bool] = None


def public_ip(ip: Optional[str]) -> Optional[str]:
    """A public IPv4/IPv6 address (IPv4-mapped IPv6 unwrapped), or None."""
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_reserved:
        return None
    if isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"):
        return None  # carrier-grade NAT
    return str(addr)


def bind_client_ip(ip: Optional[str]) -> "contextvars.Token[Optional[str]]":
    """Set the end user's address; pass the token to :func:`unbind_client_ip`."""
    return _var.set(public_ip(ip))


def unbind_client_ip(token: "contextvars.Token[Optional[str]]") -> None:
    try:
        _var.reset(token)
    except ValueError:  # pragma: no cover - token from another context
        _var.set(None)


@contextmanager
def client_ip(ip: Optional[str]) -> Iterator[None]:
    """Run a block with ``ip`` as the end user's address — ``None`` for none::

        with client_ip(job.client_ip):
            client.messaging.verifications.start(job.phone)
    """
    token = bind_client_ip(ip)
    try:
        yield
    finally:
        unbind_client_ip(token)


def current_client_ip() -> Optional[str]:
    """The end user's address for this request, task or block, or None."""
    return _var.get()


def set_forwarding(enabled: bool) -> None:
    """Turn automatic forwarding on or off (also ``NEXUS_FORWARD_CLIENT_IP``)."""
    global _forwarding
    _forwarding = bool(enabled)


def _forwarding_on() -> bool:
    if _forwarding is not None:
        return _forwarding
    value = os.environ.get("NEXUS_FORWARD_CLIENT_IP", "").strip().lower()
    return value not in ("0", "false", "no", "off")


def client_ip_headers(explicit: Optional[str]) -> Dict[str, str]:
    """Headers to add: none when the caller passed an address (it goes in the body) or forwarding is off."""
    if explicit is not None or not _forwarding_on():
        return {}
    ip = current_client_ip()
    return {"X-Client-IP": ip} if ip else {}
