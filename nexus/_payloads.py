"""Request construction for every Nexus partner endpoint.

This module is the **single source of truth** for paths and wire field names.
It is pure: functions here build a :class:`Request` and never perform I/O, so
the synchronous and asynchronous clients share exactly one definition of the
API surface. If an endpoint changes, it changes here and both clients follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Union
from urllib.parse import quote

__all__ = ["Request", "compact", "RoomMessage"]


@dataclass(frozen=True)
class Request:
    """One HTTP call to the partner API.

    ``retry`` marks a request as safe to repeat (telemetry ingest and reads).
    Anything with a side effect a user would notice twice — a push send, a
    voice leg, a realtime emit — is left unretried on purpose.
    """

    method: str
    path: str
    json: Optional[Dict[str, Any]] = None
    headers: Mapping[str, str] = field(default_factory=dict)
    retry: bool = False


def compact(data: Mapping[str, Any]) -> Dict[str, Any]:
    """Drop ``None`` values — the API validates strictly and rejects explicit nulls."""
    return {k: v for k, v in data.items() if v is not None}


def _seg(value: str) -> str:
    """URL-encode one path segment."""
    return quote(str(value), safe="")


def _obj(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Coerce to a dict that serialises as a JSON object, never ``[]``."""
    return dict(value) if value else {}


# ── Realtime ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoomMessage:
    """A realtime message: the room, the event name(s), and the payload.

    ::

        from nexus import RoomMessage
        client.realtime.broadcast([
            RoomMessage("orders:1", "location", {"lat": 1}),
            RoomMessage("orders:2", ["location", "eta"], {"lat": 2}),
        ])
    """

    room: str
    events: Sequence[str]
    payload: Any = None

    def __init__(self, room: str, events: Union[str, Sequence[str]], payload: Any = None) -> None:
        object.__setattr__(self, "room", str(room))
        object.__setattr__(self, "events", [events] if isinstance(events, str) else list(events))
        object.__setattr__(self, "payload", payload)

    @classmethod
    def coerce(cls, value: Union["RoomMessage", Mapping[str, Any]]) -> "RoomMessage":
        """Accept a :class:`RoomMessage` or a loose dict."""
        if isinstance(value, RoomMessage):
            return value
        room = value.get("room") or value.get("name") or ""
        events = value.get("events") or value.get("event") or []
        return cls(str(room), events, value.get("payload"))

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.room, "events": list(self.events), "payload": self.payload}


def realtime_emit(message: RoomMessage) -> Request:
    return Request(
        "POST",
        "/partner/rooms/{0}/emit".format(_seg(message.room)),
        {"events": list(message.events), "payload": message.payload},
    )


def realtime_broadcast(messages: Iterable[Union[RoomMessage, Mapping[str, Any]]]) -> Request:
    rooms = [RoomMessage.coerce(m).to_dict() for m in messages]
    return Request("POST", "/partner/rooms/emit", {"rooms": rooms})


def realtime_register_room(name: str, type_: Optional[str] = None) -> Request:
    return Request("POST", "/partner/rooms", compact({"name": name, "type": type_}))


def realtime_list_rooms() -> Request:
    return Request("GET", "/partner/rooms", retry=True)


def realtime_delete_room(room_id: str) -> Request:
    return Request("DELETE", "/partner/rooms/{0}".format(_seg(room_id)))


def realtime_link(room_id: str, related_id: str) -> Request:
    return Request("POST", "/partner/rooms/{0}/link/{1}".format(_seg(room_id), _seg(related_id)))


def realtime_unlink(room_id: str, related_id: str) -> Request:
    return Request("POST", "/partner/rooms/{0}/unlink/{1}".format(_seg(room_id), _seg(related_id)))


def realtime_get_schema(room_id: str) -> Request:
    return Request("GET", "/partner/rooms/{0}/schema".format(_seg(room_id)), retry=True)


def realtime_set_schema(room_id: str, schema: Mapping[str, Any]) -> Request:
    return Request("POST", "/partner/rooms/{0}/schema".format(_seg(room_id)), {"schema": dict(schema)})


def realtime_enable_schema(room_id: str, enabled: bool) -> Request:
    return Request(
        "POST", "/partner/rooms/{0}/schema/enable".format(_seg(room_id)), {"enabled": bool(enabled)}
    )


def realtime_clear_schema(room_id: str) -> Request:
    return Request("DELETE", "/partner/rooms/{0}/schema".format(_seg(room_id)))


def realtime_related(name: str) -> Request:
    return Request("GET", "/partner/rooms/{0}/related".format(_seg(name)), retry=True)


# ── Sessions ─────────────────────────────────────────────────────────────────


def sessions_identify(
    distinct_id: str,
    email: Optional[str],
    name: Optional[str],
    phone: Optional[str],
    traits: Optional[Mapping[str, Any]],
) -> Request:
    return Request(
        "POST",
        "/partner/sessions/identify",
        compact(
            {
                "distinctId": distinct_id,
                "email": email,
                "name": name,
                "phone": phone,
                "traits": dict(traits) if traits else None,
            }
        ),
        retry=True,
    )


_SESSION_FIELDS = (
    "distinctId",
    "email",
    "name",
    "traits",
    "deviceKey",
    "sessionKey",
    "browser",
    "appVersion",
    "country",
    "entryUrl",
    "osType",
    "osVersion",
)


def sessions_track(fields: Mapping[str, Any]) -> Request:
    body = compact({k: fields.get(k) for k in _SESSION_FIELDS})
    return Request("POST", "/partner/sessions/track", body, retry=True)


# ── Events ───────────────────────────────────────────────────────────────────

_INGEST_CONTEXT = (
    "release",
    "distinctId",
    "sessionKey",
    "deviceKey",
    "osType",
    "osVersion",
    "browser",
    "appVersion",
)


def events_batch(events: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> Request:
    body = compact({k: context.get(k) for k in _INGEST_CONTEXT})
    body["events"] = [dict(e) for e in events]
    return Request("POST", "/partner/events", body, retry=True)


def event_item(
    name: str, properties: Optional[Mapping[str, Any]], timestamp: Optional[str]
) -> Dict[str, Any]:
    """One entry of the ``events`` array."""
    return compact(
        {
            "name": name,
            "properties": dict(properties) if properties else None,
            "timestamp": timestamp,
        }
    )


# ── Logs ─────────────────────────────────────────────────────────────────────


def logs_batch(logs: Sequence[Mapping[str, Any]], context: Mapping[str, Any]) -> Request:
    body = compact({k: context.get(k) for k in _INGEST_CONTEXT})
    body["logs"] = [dict(line) for line in logs]
    return Request("POST", "/partner/logs", body, retry=True)


def log_item(
    level: str,
    message: str,
    source: Optional[str],
    context: Optional[Mapping[str, Any]],
    timestamp: Optional[str],
) -> Dict[str, Any]:
    """One entry of the ``logs`` array."""
    return compact(
        {
            "level": level,
            "message": message,
            "source": source,
            "context": dict(context) if context else None,
            "timestamp": timestamp,
        }
    )


# ── Errors ───────────────────────────────────────────────────────────────────

_ERROR_FIELDS = (
    "message",
    "type",
    "level",
    "handled",
    "fingerprint",
    "stack",
    "context",
    "release",
    "url",
    "distinctId",
    "sessionKey",
    "deviceKey",
    "osType",
    "osVersion",
    "browser",
    "appVersion",
)


def errors_capture(fields: Mapping[str, Any]) -> Request:
    return Request("POST", "/partner/errors", compact({k: fields.get(k) for k in _ERROR_FIELDS}), retry=True)


# ── Feature flags ────────────────────────────────────────────────────────────


def flags_evaluate(distinct_id: str, properties: Optional[Mapping[str, Any]]) -> Request:
    return Request(
        "POST",
        "/partner/flags/evaluate",
        compact({"distinctId": distinct_id, "properties": dict(properties) if properties else None}),
        retry=True,
    )


# ── Remote Config ────────────────────────────────────────────────────────────

_RC_CONTEXT = (
    "appInstanceId",
    "appVersion",
    "appBuild",
    "platform",
    "osVersion",
    "country",
    "language",
    "firstOpenTime",
    "userProperties",
)


def remote_config_fetch(context: Mapping[str, Any], etag: Optional[str]) -> Request:
    body = compact({k: context.get(k) for k in _RC_CONTEXT})
    headers = {"If-None-Match": etag} if etag else {}
    return Request("POST", "/partner/remote-config/fetch", body, headers, retry=True)


def remote_config_set_parameter(
    key: str,
    value_type: str,
    default_value: Any,
    conditional_values: Optional[Mapping[str, Any]],
    description: Optional[str],
) -> Request:
    return Request(
        "PUT",
        "/partner/remote-config/parameters",
        compact(
            {
                "key": key,
                "valueType": value_type,
                "defaultValue": default_value,
                "conditionalValues": dict(conditional_values) if conditional_values else None,
                "description": description,
            }
        ),
    )


def remote_config_delete_parameter(key: str) -> Request:
    return Request("DELETE", "/partner/remote-config/parameters/{0}".format(_seg(key)))


def remote_config_publish(description: Optional[str]) -> Request:
    return Request("POST", "/partner/remote-config/publish", compact({"description": description}))


# ── Deep links ───────────────────────────────────────────────────────────────

_LINK_FIELDS = (
    "type",
    "name",
    "clickId",
    "distinctId",
    "deviceId",
    "sessionKey",
    "platform",
    "osType",
    "country",
    "revenue",
    "properties",
)


def links_attribute(fields: Mapping[str, Any]) -> Request:
    return Request(
        "POST", "/partner/links/attribute", compact({k: fields.get(k) for k in _LINK_FIELDS}), retry=True
    )


# ── Surveys ──────────────────────────────────────────────────────────────────


def surveys_active(fields: Mapping[str, Any]) -> Request:
    body = compact(
        {
            "distinctId": fields.get("distinctId"),
            "deviceKey": fields.get("deviceKey"),
            "properties": fields.get("properties") or None,
            "deviceType": fields.get("deviceType"),
            "osType": fields.get("osType"),
        }
    )
    return Request("POST", "/partner/surveys/active", body, retry=True)


_SURVEY_RESPONSE_FIELDS = (
    "surveyId",
    "responseId",
    "completed",
    "dismissed",
    "iterationKey",
    "distinctId",
    "sessionKey",
    "deviceKey",
    "osType",
    "osVersion",
    "browser",
    "appVersion",
    "country",
    "url",
)


def surveys_respond(survey_id: str, answers: Optional[Mapping[str, Any]], fields: Mapping[str, Any]) -> Request:
    body = compact({k: fields.get(k) for k in _SURVEY_RESPONSE_FIELDS})
    body["surveyId"] = survey_id
    # Must serialise as a JSON object, never [] — the API validates it.
    body["answers"] = _obj(answers)
    return Request("POST", "/partner/surveys/responses", body)


# ── Push ─────────────────────────────────────────────────────────────────────


def push_send(payload: Mapping[str, Any]) -> Request:
    return Request("POST", "/partner/push/send", compact(payload))


_PUSH_TOKEN_FIELDS = (
    "token",
    "platform",
    "provider",
    "distinctId",
    "deviceKey",
    "appVersion",
    "osType",
    "osVersion",
    "lang",
    "tags",
)


def push_register(fields: Mapping[str, Any]) -> Request:
    return Request("POST", "/partner/push/register", compact({k: fields.get(k) for k in _PUSH_TOKEN_FIELDS}))


def push_unregister(token: str) -> Request:
    return Request("POST", "/partner/push/unregister", {"token": token})


def push_opened(fields: Mapping[str, Any]) -> Request:
    return Request("POST", "/partner/push/opened", compact(dict(fields)), retry=True)


# ── In-app messages ──────────────────────────────────────────────────────────


def inapp_active(distinct_id: Optional[str], device_key: Optional[str]) -> Request:
    return Request(
        "POST",
        "/partner/inapp/active",
        compact({"distinctId": distinct_id, "deviceKey": device_key}),
        retry=True,
    )


def inapp_event(
    message_id: str, type_: str, button_id: Optional[str], distinct_id: Optional[str]
) -> Request:
    return Request(
        "POST",
        "/partner/inapp/event",
        compact(
            {
                "messageId": message_id,
                "type": type_,
                "buttonId": button_id,
                "distinctId": distinct_id,
            }
        ),
    )


# ── Live Activities ──────────────────────────────────────────────────────────


def live_activity_start(
    activity_type: str,
    activity_id: str,
    content_state: Optional[Mapping[str, Any]],
    distinct_ids: Optional[Sequence[str]],
    attributes: Optional[Mapping[str, Any]],
    stale_date: Optional[str],
    priority: Optional[int],
) -> Request:
    return Request(
        "POST",
        "/partner/live-activities/start",
        compact(
            {
                "activityType": activity_type,
                "activityId": activity_id,
                "contentState": _obj(content_state),
                "distinctIds": list(distinct_ids) if distinct_ids else None,
                "attributes": dict(attributes) if attributes else None,
                "staleDate": stale_date,
                "priority": priority,
            }
        ),
    )


def live_activity_update(
    activity_id: str,
    content_state: Optional[Mapping[str, Any]],
    stale_date: Optional[str],
    priority: Optional[int],
) -> Request:
    return Request(
        "POST",
        "/partner/live-activities/{0}/update".format(_seg(activity_id)),
        compact({"contentState": _obj(content_state), "staleDate": stale_date, "priority": priority}),
    )


def live_activity_end(
    activity_id: str,
    content_state: Optional[Mapping[str, Any]],
    dismissal_date: Optional[str],
    data: Optional[Mapping[str, Any]] = None,
) -> Request:
    return Request(
        "POST",
        "/partner/live-activities/{0}/end".format(_seg(activity_id)),
        compact(
            {
                "data": dict(data) if data else None,
                "contentState": dict(content_state) if content_state else None,
                "dismissalDate": dismissal_date,
            }
        ),
    )


def live_activity_start_template(
    template: Union[str, Mapping[str, Any]],
    activity_id: str,
    data: Optional[Mapping[str, Any]],
    distinct_ids: Optional[Sequence[str]],
    lang: Optional[str],
    alert: Optional[Mapping[str, Any]],
    attributes: Optional[Mapping[str, Any]],
    stale_date: Optional[str],
    priority: Optional[int],
) -> Request:
    """A templated start: ``template`` is a console template key or an inline design.
    No ``activityType`` — the server targets the SDK widget's type."""
    return Request(
        "POST",
        "/partner/live-activities/start",
        compact(
            {
                "template": template if isinstance(template, str) else dict(template),
                "activityId": activity_id,
                "data": _obj(data),
                "distinctIds": list(distinct_ids) if distinct_ids else None,
                "lang": lang,
                "alert": dict(alert) if alert else None,
                "attributes": dict(attributes) if attributes else None,
                "staleDate": stale_date,
                "priority": priority,
            }
        ),
    )


def live_activity_update_data(
    activity_id: str,
    data: Optional[Mapping[str, Any]],
    stale_date: Optional[str],
    priority: Optional[int],
) -> Request:
    """A templated update: partial ``data`` the server merges (``None`` inside it clears a field)."""
    return Request(
        "POST",
        "/partner/live-activities/{0}/update".format(_seg(activity_id)),
        compact({"data": _obj(data), "staleDate": stale_date, "priority": priority}),
    )


def live_activity_push_token(
    activity_type: str, token: str, distinct_id: Optional[str], device_key: Optional[str]
) -> Request:
    return Request(
        "POST",
        "/partner/live-activities/push-token",
        compact(
            {
                "activityType": activity_type,
                "token": token,
                "distinctId": distinct_id,
                "deviceKey": device_key,
            }
        ),
    )


def live_activity_register(
    activity_id: str,
    activity_type: str,
    platform: str,
    update_token: Optional[str],
    distinct_id: Optional[str],
    content_state: Optional[Mapping[str, Any]],
) -> Request:
    return Request(
        "POST",
        "/partner/live-activities/register",
        compact(
            {
                "activityId": activity_id,
                "activityType": activity_type,
                "platform": platform,
                "updateToken": update_token,
                "distinctId": distinct_id,
                "contentState": dict(content_state) if content_state else None,
            }
        ),
    )


def live_activity_event(activity_id: str, type_: str, platform: Optional[str]) -> Request:
    return Request(
        "POST",
        "/partner/live-activities/event",
        compact({"activityId": activity_id, "type": type_, "platform": platform}),
    )


# ── Session replay ───────────────────────────────────────────────────────────

_REPLAY_FIELDS = (
    "recordingId",
    "distinctId",
    "sessionKey",
    "deviceKey",
    "href",
    "width",
    "height",
    "osType",
    "osVersion",
    "browser",
    "appVersion",
)


def replay_ingest(events: Sequence[Mapping[str, Any]], fields: Mapping[str, Any]) -> Request:
    body = compact({k: fields.get(k) for k in _REPLAY_FIELDS})
    body["events"] = [dict(e) for e in events]
    return Request("POST", "/partner/replay", body, retry=True)


# ── Voice ────────────────────────────────────────────────────────────────────


def voice_create_call(
    direction: Optional[str],
    application_id: Optional[str],
    proxy_identity_id: Optional[str],
    recording_mode: Optional[str],
    metadata: Optional[Mapping[str, Any]],
) -> Request:
    return Request(
        "POST",
        "/partner/voice/calls",
        compact(
            {
                "direction": direction,
                "applicationId": application_id,
                "proxyIdentityId": proxy_identity_id,
                "recordingMode": recording_mode,
                "metadata": dict(metadata) if metadata else None,
            }
        ),
    )


def voice_add_leg(fields: Mapping[str, Any]) -> Request:
    body = compact(
        {
            "sessionId": fields.get("sessionId"),
            "role": fields.get("role"),
            "endpointType": fields.get("endpointType"),
            "direction": fields.get("direction"),
            "address": fields.get("address"),
            "callerId": fields.get("callerId"),
            "identityId": fields.get("identityId"),
        }
    )
    return Request("POST", "/partner/voice/legs", body)


def voice_leg_action(action: str, leg_id: str, reason: Optional[str] = None) -> Request:
    body: Dict[str, Any] = {"legId": leg_id}
    if reason is not None:
        body["reason"] = reason
    return Request("POST", "/partner/voice/legs/{0}".format(action), body)


def voice_dtmf(session_id: str, leg_id: str, digit: str, duration_ms: Optional[int]) -> Request:
    return Request(
        "POST",
        "/partner/voice/dtmf",
        compact({"sessionId": session_id, "legId": leg_id, "digit": digit, "durationMs": duration_ms}),
    )


def voice_ivr_start(flow_id: str, application_id: Optional[str]) -> Request:
    return Request(
        "POST", "/partner/voice/ivr/start", compact({"flowId": flow_id, "applicationId": application_id})
    )


def voice_ivr_advance(session_id: str) -> Request:
    return Request("POST", "/partner/voice/ivr/advance", {"sessionId": session_id})


def voice_ivr_input(session_id: str, digit: str) -> Request:
    return Request("POST", "/partner/voice/ivr/input", {"sessionId": session_id, "digit": digit})


def voice_register_device(
    identity_id: str,
    device_id: str,
    platform: str,
    voip_token: Optional[str],
    fcm_token: Optional[str],
) -> Request:
    return Request(
        "POST",
        "/partner/voice/devices",
        compact(
            {
                "identityId": identity_id,
                "deviceId": device_id,
                "platform": platform,
                "voipToken": voip_token,
                "fcmToken": fcm_token,
            }
        ),
    )


def voice_presence(identity_id: str, device_id: str, status: str) -> Request:
    return Request(
        "POST",
        "/partner/voice/presence",
        {"identityId": identity_id, "deviceId": device_id, "status": status},
    )


def voice_quality(fields: Mapping[str, Any]) -> Request:
    allowed = (
        "sessionId",
        "legId",
        "rttMs",
        "jitterMs",
        "packetLoss",
        "bitrateKbps",
        "mos",
        "codec",
        "candidateType",
    )
    return Request("POST", "/partner/voice/quality", compact({k: fields.get(k) for k in allowed}), retry=True)


def voice_transfer_blind(
    session_id: str,
    leg_id: str,
    target_type: str,
    target_address: Optional[str],
    target_identity_id: Optional[str],
    caller_id: Optional[str],
) -> Request:
    return Request(
        "POST",
        "/partner/voice/transfer/blind",
        compact(
            {
                "sessionId": session_id,
                "legId": leg_id,
                "targetType": target_type,
                "targetAddress": target_address,
                "targetIdentityId": target_identity_id,
                "callerId": caller_id,
            }
        ),
    )


def voice_transfer_warm_start(
    session_id: str,
    other_party_leg_id: str,
    target_type: str,
    target_address: Optional[str],
    target_identity_id: Optional[str],
    caller_id: Optional[str],
) -> Request:
    return Request(
        "POST",
        "/partner/voice/transfer/warm/start",
        compact(
            {
                "sessionId": session_id,
                "otherPartyLegId": other_party_leg_id,
                "targetType": target_type,
                "targetAddress": target_address,
                "targetIdentityId": target_identity_id,
                "callerId": caller_id,
            }
        ),
    )


def voice_transfer_warm_finish(action: str, other_party_leg_id: str, leg_id: str) -> Request:
    return Request(
        "POST",
        "/partner/voice/transfer/warm/{0}".format(action),
        {"otherPartyLegId": other_party_leg_id, "legId": leg_id},
    )


def voice_conference_mute(leg_id: str, muted: bool) -> Request:
    return Request("POST", "/partner/voice/conference/mute", {"legId": leg_id, "muted": bool(muted)})


def voice_conference_remove(session_id: str, leg_id: str) -> Request:
    return Request("POST", "/partner/voice/conference/remove", {"sessionId": session_id, "legId": leg_id})


# ── Shield (app & device integrity) ──────────────────────────────────────────


def shield_verify(fields: Mapping[str, Any]) -> Request:
    # Never retried: verify burns the proof's jti, so a retry of a request that
    # did reach the server would come back as `proof_replayed`.
    return Request("POST", "/partner/shield/verify", compact(fields))


# ── DocStore (document database) ─────────────────────────────────────────────
# Auth is per call, not per client: the `headers` carry either the Databases
# service key (rules bypassed) or an end user's bearer token (rules apply).
# Reads are retried; writes are not (an `$inc` must never apply twice).


def _ds(path: str) -> str:
    return "/partner/docstore" + path


def _ds_doc(collection: str, doc_id: str) -> str:
    return _ds("/collections/{0}/docs/{1}".format(_seg(collection), _seg(doc_id)))


def _qs(params: Mapping[str, Any]) -> str:
    from urllib.parse import urlencode

    clean = {k: v for k, v in params.items() if v is not None}
    return "?" + urlencode(clean) if clean else ""


def docstore_get(collection: str, doc_id: str, headers: Mapping[str, str], *, expand: Optional[Sequence[str]] = None, select: Optional[Sequence[str]] = None, as_of: Optional[str] = None) -> Request:
    query = _qs({"expand": ",".join(expand) if expand else None, "select": ",".join(select) if select else None, "asOf": as_of})
    return Request("GET", _ds_doc(collection, doc_id) + query, None, headers, retry=True)


def docstore_query(collection: str, body: Mapping[str, Any], headers: Mapping[str, str]) -> Request:
    return Request("POST", _ds("/collections/{0}/query".format(_seg(collection))), compact(body), headers, retry=True)


def docstore_aggregate(collection: str, body: Mapping[str, Any], headers: Mapping[str, str]) -> Request:
    return Request("POST", _ds("/collections/{0}/aggregate".format(_seg(collection))), compact(body), headers, retry=True)


def docstore_create(collection: str, data: Mapping[str, Any], headers: Mapping[str, str], doc_id: Optional[str] = None) -> Request:
    return Request("POST", _ds("/collections/{0}/docs".format(_seg(collection))), compact({"id": doc_id, "data": dict(data)}), headers)


def docstore_set(collection: str, doc_id: str, data: Mapping[str, Any], headers: Mapping[str, str], *, merge: bool = False, if_version: Optional[int] = None) -> Request:
    body = compact({"data": dict(data), "merge": True if merge else None, "precondition": {"version": if_version} if if_version is not None else None})
    return Request("PUT", _ds_doc(collection, doc_id), body, headers)


def docstore_update(collection: str, doc_id: str, update: Mapping[str, Any], headers: Mapping[str, str], *, if_version: Optional[int] = None, upsert: bool = False) -> Request:
    body = compact({"update": dict(update), "upsert": True if upsert else None, "precondition": {"version": if_version} if if_version is not None else None})
    return Request("PATCH", _ds_doc(collection, doc_id), body, headers)


def docstore_delete(collection: str, doc_id: str, headers: Mapping[str, str], *, if_version: Optional[int] = None) -> Request:
    return Request("DELETE", _ds_doc(collection, doc_id) + _qs({"ifVersion": if_version}), None, headers)


def docstore_commit(operations: Sequence[Mapping[str, Any]], headers: Mapping[str, str], *, mode: Optional[str] = None) -> Request:
    return Request("POST", _ds("/commit"), compact({"operations": [dict(o) for o in operations], "mode": mode}), headers)


def docstore_history(collection: str, doc_id: str, headers: Mapping[str, str], *, limit: Optional[int] = None, before_version: Optional[int] = None) -> Request:
    return Request("GET", _ds_doc(collection, doc_id) + "/history" + _qs({"limit": limit, "beforeVersion": before_version}), None, headers, retry=True)


def docstore_restore(collection: str, doc_id: str, version: int, headers: Mapping[str, str]) -> Request:
    return Request("POST", _ds_doc(collection, doc_id) + "/restore", {"version": int(version)}, headers)


def docstore_restore_collection(collection: str, as_of: str, headers: Mapping[str, str], *, dry_run: bool = False) -> Request:
    return Request("POST", _ds("/collections/{0}/restore".format(_seg(collection))), {"asOf": as_of, "dryRun": bool(dry_run)}, headers)


def docstore_changes(headers: Mapping[str, str], *, since: int = 0, collections: Optional[Sequence[str]] = None, limit: Optional[int] = None) -> Request:
    query = _qs({"since": int(since), "collections": ",".join(collections) if collections else None, "limit": limit})
    return Request("GET", _ds("/changes") + query, None, headers, retry=True)


def docstore_deploy_collection(name: str, config: Mapping[str, Any], headers: Mapping[str, str]) -> Request:
    return Request("PUT", _ds("/collections/{0}".format(_seg(name))), dict(config), headers, retry=True)


def docstore_list_collections(headers: Mapping[str, str]) -> Request:
    return Request("GET", _ds("/collections"), None, headers, retry=True)
