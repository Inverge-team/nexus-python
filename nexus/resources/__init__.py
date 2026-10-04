"""Resource namespaces — one per Nexus product, hung off the client."""

from .base import Resource
from .errors import Errors
from .events import Events
from .flags import Flags
from .inapp import InApp
from .links import Links
from .live_activities import LiveActivities
from .logs import Logs
from .push import Push
from .realtime import Realtime
from .remote_config import RemoteConfig
from .replay import Replay
from .sessions import Sessions
from .surveys import Surveys
from .voice import Voice

__all__ = [
    "Resource",
    "Errors",
    "Events",
    "Flags",
    "InApp",
    "Links",
    "LiveActivities",
    "Logs",
    "Push",
    "Realtime",
    "RemoteConfig",
    "Replay",
    "Sessions",
    "Surveys",
    "Voice",
]
