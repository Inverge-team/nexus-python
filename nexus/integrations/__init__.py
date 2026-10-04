"""First-class integrations for the common Python frameworks.

Each one does the same four things, idiomatically for its framework:

1. builds (or reuses) a :class:`nexus.NexusClient`,
2. binds per-request identity into :mod:`nexus.context` so every event, log and
   error captured during the request is correlated automatically,
3. optionally tracks a request event and refreshes the Nexus session,
4. reports unhandled exceptions.

======================  ==========================================
Framework               Import
======================  ==========================================
Django                  ``"nexus.integrations.django"`` in ``INSTALLED_APPS``
Flask                   ``from nexus.integrations.flask import NexusFlask``
FastAPI / Starlette     ``from nexus.integrations.fastapi import NexusMiddleware``
Any ASGI app            ``from nexus.integrations.asgi import NexusASGIMiddleware``
Any WSGI app            ``from nexus.integrations.wsgi import NexusWSGIMiddleware``
Celery                  ``from nexus.integrations.celery import install_celery``
======================  ==========================================

Anything not listed is still covered: if it speaks WSGI (Pyramid, Bottle,
CherryPy, web2py) or ASGI (Quart, Litestar, Sanic in ASGI mode, Falcon ASGI),
use the generic middleware; otherwise use the module-level client from
:func:`nexus.init` plus :class:`nexus.NexusLogHandler`.
"""

from __future__ import annotations

__all__ = ["RequestTracker", "RequestInfo"]


def __getattr__(name: str):  # pragma: no cover - thin lazy re-export
    if name in __all__:
        from . import _common

        return getattr(_common, name)
    raise AttributeError("module 'nexus.integrations' has no attribute {0!r}".format(name))
