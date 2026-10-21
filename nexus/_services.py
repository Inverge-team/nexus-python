"""Per-service on/off switches.

Turn a Nexus product off without removing the code that uses it. A switched-off
service makes no network call and never raises: writes are skipped and reads
return their neutral value (flags → defaults, Remote Config → defaults, lists →
empty).

From the environment (all default to on)::

    NEXUS_ENABLED=false                  # everything off
    NEXUS_PUSH_ENABLED=false             # only push off
    NEXUS_LIVE_ACTIVITIES_ENABLED=false  # only Live Activities off

Or in code: ``NexusClient(..., services={"push": False})`` / ``enabled=False``.
Off wins — a service is on only if neither the code nor the environment turns
it off, so ops can always switch one off with an environment variable.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

#: Service → the environment variable that switches it.
SERVICE_ENV: Dict[str, str] = {
    "realtime": "NEXUS_REALTIME_ENABLED",
    "events": "NEXUS_EVENTS_ENABLED",
    "errors": "NEXUS_ERRORS_ENABLED",
    "logs": "NEXUS_LOGS_ENABLED",
    "sessions": "NEXUS_SESSIONS_ENABLED",
    "flags": "NEXUS_FLAGS_ENABLED",
    "remote_config": "NEXUS_REMOTE_CONFIG_ENABLED",
    "links": "NEXUS_LINKS_ENABLED",
    "surveys": "NEXUS_SURVEYS_ENABLED",
    "push": "NEXUS_PUSH_ENABLED",
    "in_app": "NEXUS_IN_APP_ENABLED",
    "live_activities": "NEXUS_LIVE_ACTIVITIES_ENABLED",
    "replay": "NEXUS_REPLAY_ENABLED",
    "voice": "NEXUS_VOICE_ENABLED",
}

#: The master switch.
ALL_ENV = "NEXUS_ENABLED"

#: ``/partner/<segment>`` → service.
_PATHS: Dict[str, str] = {
    "rooms": "realtime",
    "events": "events",
    "errors": "errors",
    "logs": "logs",
    "sessions": "sessions",
    "flags": "flags",
    "remote-config": "remote_config",
    "links": "links",
    "surveys": "surveys",
    "push": "push",
    "inapp": "in_app",
    "live-activities": "live_activities",
    "replay": "replay",
    "voice": "voice",
}


def service_for_path(path: str) -> Optional[str]:
    """The service a ``/partner/...`` path belongs to, or None for unswitched paths."""
    p = path.lstrip("/")
    if p.startswith("partner/"):
        p = p[len("partner/"):]
    return _PATHS.get(p.split("/", 1)[0])


def is_on(value: Any) -> bool:
    """``false`` / ``0`` / ``off`` / ``no`` = off; anything else, or unset, = on."""
    if value is None or value == "":
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "off", "no")


#: The other Nexus SDKs' spellings, so one shared config blob drives any of them.
_ALIASES = {"remoteConfig": "remote_config", "inApp": "in_app", "liveActivities": "live_activities"}


def normalize(services: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Accept camelCase keys too (``liveActivities`` → ``live_activities``)."""
    return {_ALIASES.get(k, k): v for k, v in (services or {}).items()}


def check_names(services: Mapping[str, Any]) -> None:
    """Reject unknown service names — a typo must not silently leave one on."""
    unknown = [k for k in services if k not in SERVICE_ENV]
    if unknown:
        from .exceptions import ConfigurationError

        raise ConfigurationError(
            "Unknown Nexus service(s) %s. Known: %s." % (", ".join(sorted(unknown)), ", ".join(SERVICE_ENV))
        )


def resolve(
    services: Optional[Mapping[str, Any]],
    enabled: Any,
    env: Mapping[str, str],
) -> Tuple[bool, Dict[str, str]]:
    """Combine code + environment, off wins.

    Returns ``(all_on, off)`` where ``off`` maps each switched-off service to
    the reason (for the diagnostic log).
    """
    services = normalize(services)
    check_names(services)
    if not is_on(enabled):
        return False, {s: "enabled=False" for s in SERVICE_ENV}
    if not is_on(env.get(ALL_ENV)):
        return False, {s: "%s=false" % ALL_ENV for s in SERVICE_ENV}
    off: Dict[str, str] = {}
    for service, var in SERVICE_ENV.items():
        if service in services and not is_on(services[service]):
            off[service] = "services[%r]=False" % service
        elif not is_on(env.get(var)):
            off[service] = "%s=false" % var
    return True, off
