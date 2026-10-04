"""Django integration.

``settings.py``::

    INSTALLED_APPS = [
        ...,
        "nexus.integrations.django",
    ]

    MIDDLEWARE = [
        "nexus.integrations.django.NexusMiddleware",   # near the top
        ...,
    ]

    NEXUS = {
        "API_KEY": os.environ["NEXUS_API_KEY"],
        "TRACK_REQUESTS": True,     # one event per request
        "TRACK_SESSIONS": False,    # also refresh the Nexus session (extra call)
        "CAPTURE_ERRORS": True,     # report unhandled exceptions
        "LOGGING": False,           # forward Django logging to Nexus logs
        "LOG_LEVEL": "INFO",
        "RELEASE": os.environ.get("GIT_SHA"),
    }

Then anywhere in your project::

    import nexus

    nexus.track("order_placed", {"total": 42})
    nexus.get_client().push.send_to_user(str(user.pk), title="Your order shipped")

The middleware binds ``request.user`` into :mod:`nexus.context`, so telemetry
raised deep in a service layer is still attributed to the right person.

Flat settings (``NEXUS_API_KEY``, ``NEXUS_BASE_URL``, …) work too, which suits
``django-environ`` setups.
"""

from __future__ import annotations

from .middleware import NexusMiddleware

__all__ = ["NexusMiddleware", "NexusConfigApp"]


def __getattr__(name: str):  # pragma: no cover - avoids importing apps early
    if name == "NexusConfigApp":
        from .apps import NexusConfigApp as app_config

        return app_config
    raise AttributeError(name)
