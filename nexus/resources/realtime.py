"""Realtime rooms — server-side emit and room management.

Emitting broadcasts to every client currently joined to the room. Rooms are
**environment-scoped**: a client joined with a key from another environment will
never see your emit, which is the usual cause of "the message just doesn't
arrive".

To *receive* events in Python (a worker that reacts to room traffic), use
:class:`nexus.RealtimeSocket` — it needs ``python-socketio``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from .. import _payloads as p
from .._payloads import RoomMessage
from .base import Resource

__all__ = ["Realtime"]

Events = Union[str, Sequence[str]]


class Realtime(Resource):
    """``client.realtime``"""

    def emit(
        self,
        room: Union[str, RoomMessage],
        events: Events = (),
        payload: Any = None,
    ) -> Dict[str, Any]:
        """Emit one or more named events to a room.

        ::

            client.realtime.emit("orders:42", "status", {"state": "shipped"})
            client.realtime.emit("orders:42", ["status", "updated"], {"state": "shipped"})
            client.realtime.emit(RoomMessage("orders:42", "status", {"state": "shipped"}))

        Returns the ack: ``{ok, room, related, events, recipients}``.
        """
        message = room if isinstance(room, RoomMessage) else RoomMessage(room, events, payload)
        return self._dict(self._send(p.realtime_emit(message)))

    def broadcast(
        self, messages: Iterable[Union[RoomMessage, Mapping[str, Any]]]
    ) -> Dict[str, Any]:
        """Send to many rooms, each with its own events/payload, in one request."""
        items = list(messages)
        if not items:
            return {}
        return self._dict(self._send(p.realtime_broadcast(items)))

    def emit_to_rooms(
        self, rooms: Sequence[str], events: Events, payload: Any = None
    ) -> Dict[str, Any]:
        """The same event(s) and payload to several rooms, in one request."""
        return self.broadcast([RoomMessage(room, events, payload) for room in rooms])

    # ── room registry ────────────────────────────────────────────────────────

    def register_room(self, name: str, type: Optional[str] = None) -> Dict[str, Any]:  # noqa: A002
        """Create a room up front (they are also auto-created on join/emit)."""
        return self._dict(self._send(p.realtime_register_room(name, type)))

    def rooms(self) -> List[Any]:
        """The environment's registered rooms."""
        return self._list(self._send(p.realtime_list_rooms()), "rooms")

    def delete_room(self, room_id: str) -> None:
        self._send(p.realtime_delete_room(room_id))

    def link(self, room_id: str, related_id: str) -> None:
        """Link two rooms so an emit to one also reaches the other."""
        self._send(p.realtime_link(room_id, related_id))

    def unlink(self, room_id: str, related_id: str) -> None:
        self._send(p.realtime_unlink(room_id, related_id))

    def related(self, name: str) -> Any:
        """Rooms linked to this one."""
        return self._send(p.realtime_related(name))

    # ── payload schema ───────────────────────────────────────────────────────

    def schema(self, room_id: str) -> Dict[str, Any]:
        """The room's JSON Schema, if one is set."""
        return self._dict(self._send(p.realtime_get_schema(room_id)))

    def set_schema(self, room_id: str, schema: Mapping[str, Any]) -> Dict[str, Any]:
        """Attach a JSON Schema that payloads to this room must satisfy."""
        return self._dict(self._send(p.realtime_set_schema(room_id, schema)))

    def enable_schema(self, room_id: str, enabled: bool = True) -> Dict[str, Any]:
        """Turn schema validation on or off."""
        return self._dict(self._send(p.realtime_enable_schema(room_id, enabled)))

    def clear_schema(self, room_id: str) -> None:
        self._send(p.realtime_clear_schema(room_id))
