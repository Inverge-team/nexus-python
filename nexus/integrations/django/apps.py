"""The Django ``AppConfig`` — builds the client once per process."""

from __future__ import annotations

import logging
from typing import Any

from django.apps import AppConfig

from ..._logging import logger

__all__ = ["NexusConfigApp"]


class NexusConfigApp(AppConfig):
    """Add ``"nexus.integrations.django"`` to ``INSTALLED_APPS``."""

    name = "nexus.integrations.django"
    label = "nexus"
    verbose_name = "Nexus"

    def ready(self) -> None:
        from .conf import client_options, django_settings

        values = django_settings()
        if not values.get("AUTO_INIT", True):
            return

        from ... import init, is_initialized

        if not is_initialized():
            options = client_options(values)
            if not options.get("api_key"):
                # No key configured anywhere — stay silent rather than crash a
                # management command that has nothing to do with telemetry.
                import os

                if not os.environ.get("NEXUS_API_KEY"):
                    logger.debug("nexus: no API key in settings or environment; not initialising")
                    return
            # capture_errors covers what the middleware cannot: management
            # commands, shells, and anything outside the request cycle. Request
            # errors are reported by NexusMiddleware, not by the excepthook.
            init(capture_errors=True, **options)

        if values.get("LOGGING"):
            self._install_logging(values)

    def _install_logging(self, values: Any) -> None:
        from ... import NexusLogHandler, get_client

        level = values.get("LOG_LEVEL", "INFO")
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.INFO)
        target = logging.getLogger(values.get("LOGGER_NAME") or "")
        if any(isinstance(h, NexusLogHandler) for h in target.handlers):
            return
        target.addHandler(NexusLogHandler(get_client(required=False), level=level))
