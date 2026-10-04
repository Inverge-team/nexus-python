"""Feature flags — evaluate the environment's active flags for one person."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .. import _payloads as p
from .._logging import logger
from ..exceptions import NexusError
from ..models import FlagEvaluation
from .base import Resource

__all__ = ["Flags"]


class Flags(Resource):
    """``client.flags``

    Every helper performs one evaluation call. When you need several flags for
    the same user, call :meth:`evaluate` once and ask the result::

        flags = client.flags.evaluate("user_1", {"plan": "pro"})
        if flags.is_enabled("new_checkout"):
            ...
        theme = flags.payload("paywall")
    """

    def evaluate(
        self,
        distinct_id: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        **extra_properties: Any,
    ) -> FlagEvaluation:
        """Evaluate every active flag for a person.

        ``distinct_id`` falls back to the ambient :mod:`nexus.context`.
        """
        person = distinct_id or self._client.current_distinct_id()
        if not person:
            raise ValueError(
                "flags.evaluate() needs a distinct_id (pass one, or bind it with nexus.context)."
            )
        merged = dict(properties or {})
        merged.update(extra_properties)
        return FlagEvaluation.from_response(self._send(p.flags_evaluate(person, merged or None)))

    def all(
        self,
        distinct_id: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        **extra_properties: Any,
    ) -> Mapping[str, Any]:
        """Flat ``{key: bool | variant}`` map."""
        return self.evaluate(distinct_id, properties, **extra_properties).flags

    def _safe_evaluate(
        self,
        distinct_id: Optional[str],
        properties: Optional[Mapping[str, Any]],
        extra_properties: Mapping[str, Any],
    ) -> Optional[FlagEvaluation]:
        """Evaluate, but never let a flag outage take a page down.

        A flag lookup is a *decision point*, not a data fetch: raising would
        turn a Nexus hiccup into a 500 on a route that would otherwise render
        fine. The convenience helpers therefore log and fall back to the
        caller's default; :meth:`evaluate` still raises so you can handle it
        explicitly when you want to.
        """
        try:
            return self.evaluate(distinct_id, properties, **dict(extra_properties))
        except NexusError as exc:
            logger.warning("nexus: flag evaluation failed, using the default: %s", exc)
            return None

    def is_enabled(
        self,
        key: str,
        distinct_id: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        *,
        default: bool = False,
        **extra_properties: Any,
    ) -> bool:
        """Whether a flag is on, or ``default`` if it can't be evaluated."""
        result = self._safe_evaluate(distinct_id, properties, extra_properties)
        if result is None or not result.flags:
            return default
        return result.is_enabled(key)

    def variant(
        self,
        key: str,
        distinct_id: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        *,
        default: Optional[str] = None,
        **extra_properties: Any,
    ) -> Optional[str]:
        """The resolved variant of a multivariate flag, else ``default``."""
        result = self._safe_evaluate(distinct_id, properties, extra_properties)
        if result is None:
            return default
        return result.variant(key) or default

    def payload(
        self,
        key: str,
        distinct_id: Optional[str] = None,
        properties: Optional[Mapping[str, Any]] = None,
        *,
        default: Any = None,
        **extra_properties: Any,
    ) -> Any:
        """The flag's attached JSON payload, or ``default``.

        ``default`` is keyword-only so a third positional argument can't be
        mistaken for it — that slot is the targeting ``properties``.
        """
        result = self._safe_evaluate(distinct_id, properties, extra_properties)
        return default if result is None else result.payload(key, default)
