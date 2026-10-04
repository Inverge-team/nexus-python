"""Nexus Voice (CPaaS) control plane.

A call is a **session** with one or more **legs**. Create the session, add a leg
per participant, then drive it: ring, answer, hold, hangup, DTMF, transfer,
conference moderation. WebRTC legs come back with a media join bundle (a scoped
token + TURN config) for the client to join with; PSTN/SIP legs are dialled by
the media plane.

The tenant is always the environment behind your API key — never passed in the
body — and grants, rooms and caller-ID are decided server-side.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlencode

from .. import _payloads as p
from .base import Resource

__all__ = ["Voice"]

ROLES = ("caller", "callee", "agent", "supervisor", "conference", "ivr", "announcement")
ENDPOINTS = ("webrtc", "sip", "pstn", "app")
DIRECTIONS = ("inbound", "outbound", "internal")
RECORDING_MODES = ("disabled", "mixed", "dual_channel", "per_track")
PRESENCE = ("ONLINE", "OFFLINE", "BUSY", "AWAY", "DND", "RINGING", "IN_CALL")


class Voice(Resource):
    """``client.voice``"""

    # ── sessions & legs ──────────────────────────────────────────────────────

    def create_call(
        self,
        *,
        direction: Optional[str] = None,
        application_id: Optional[str] = None,
        proxy_identity_id: Optional[str] = None,
        recording_mode: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a call session. Returns ``{session: {...}}``.

        ``direction``: inbound | outbound | internal.
        ``recording_mode``: disabled | mixed | dual_channel | per_track.
        ``proxy_identity_id`` routes through a masking identity (number masking).
        """
        return self._dict(
            self._send(
                p.voice_create_call(direction, application_id, proxy_identity_id, recording_mode, metadata)
            )
        )

    def add_leg(
        self,
        session_id: str,
        role: str,
        endpoint_type: str,
        *,
        direction: Optional[str] = None,
        address: Optional[str] = None,
        caller_id: Optional[str] = None,
        identity_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a participant leg to a session.

        * ``webrtc`` → the response carries ``leg`` plus ``join``:
          ``{room, region, access, turn}`` — hand ``access`` and ``turn`` to
          the device SDK.
        * ``pstn`` / ``sip`` → the media plane dials ``address``; the response
          carries ``dialing``.

        A response containing ``error`` is still HTTP 200 — check for
        ``call_blocked`` (fraud policy), ``no_outbound_trunk`` and
        ``media_unavailable`` before assuming the leg is live.
        """
        if role not in ROLES:
            raise ValueError("role must be one of {0}".format(", ".join(ROLES)))
        if endpoint_type not in ENDPOINTS:
            raise ValueError("endpoint_type must be one of {0}".format(", ".join(ENDPOINTS)))
        return self._dict(
            self._send(
                p.voice_add_leg(
                    {
                        "sessionId": session_id,
                        "role": role,
                        "endpointType": endpoint_type,
                        "direction": direction,
                        "address": address,
                        "callerId": caller_id,
                        "identityId": identity_id,
                    }
                )
            )
        )

    def ring(self, leg_id: str) -> Dict[str, Any]:
        return self._dict(self._send(p.voice_leg_action("ring", leg_id)))

    def answer(self, leg_id: str) -> Dict[str, Any]:
        return self._dict(self._send(p.voice_leg_action("answer", leg_id)))

    def hold(self, leg_id: str) -> Dict[str, Any]:
        return self._dict(self._send(p.voice_leg_action("hold", leg_id)))

    def resume(self, leg_id: str) -> Dict[str, Any]:
        return self._dict(self._send(p.voice_leg_action("resume", leg_id)))

    def hangup(self, leg_id: str, reason: str = "hangup") -> Dict[str, Any]:
        return self._dict(self._send(p.voice_leg_action("hangup", leg_id, reason)))

    def dtmf(
        self, session_id: str, leg_id: str, digit: str, *, duration_ms: Optional[int] = None
    ) -> Dict[str, Any]:
        """Send a DTMF digit. If the session is in an IVR, the response's
        ``ivr`` key carries the next instruction."""
        return self._dict(self._send(p.voice_dtmf(session_id, leg_id, digit, duration_ms)))

    # ── IVR ──────────────────────────────────────────────────────────────────

    def ivr_start(self, flow_id: str, *, application_id: Optional[str] = None) -> Dict[str, Any]:
        """Start an in-app IVR flow. Returns ``{sessionId, instruction}``.

        The menu phase is Redis-only — no call session exists until the flow
        resolves to a ``connect`` instruction.
        """
        return self._dict(self._send(p.voice_ivr_start(flow_id, application_id)))

    def ivr_advance(self, session_id: str) -> Dict[str, Any]:
        """Advance the flow (e.g. after a prompt finished playing)."""
        return self._dict(self._send(p.voice_ivr_advance(session_id)))

    def ivr_input(self, session_id: str, digit: str) -> Dict[str, Any]:
        """Feed a menu keypress; returns the next ``instruction``."""
        return self._dict(self._send(p.voice_ivr_input(session_id, digit)))

    # ── devices & presence ───────────────────────────────────────────────────

    def register_device(
        self,
        identity_id: str,
        device_id: str,
        platform: str,
        *,
        voip_token: Optional[str] = None,
        fcm_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a device so incoming calls can ring it (ios | android | other)."""
        return self._dict(
            self._send(p.voice_register_device(identity_id, device_id, platform, voip_token, fcm_token))
        )

    def presence(self, identity_id: str, device_id: str, status: str) -> Dict[str, Any]:
        """Heartbeat a device's presence. Returns the identity's rolled-up status."""
        if status not in PRESENCE:
            raise ValueError("status must be one of {0}".format(", ".join(PRESENCE)))
        return self._dict(self._send(p.voice_presence(identity_id, device_id, status)))

    # ── quality ──────────────────────────────────────────────────────────────

    def report_quality(
        self,
        session_id: str,
        *,
        leg_id: Optional[str] = None,
        rtt_ms: Optional[int] = None,
        jitter_ms: Optional[int] = None,
        packet_loss: Optional[float] = None,
        bitrate_kbps: Optional[int] = None,
        mos: Optional[float] = None,
        codec: Optional[str] = None,
        candidate_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Report per-leg media quality (feeds the console's voice analytics)."""
        return self._dict(
            self._send(
                p.voice_quality(
                    {
                        "sessionId": session_id,
                        "legId": leg_id,
                        "rttMs": rtt_ms,
                        "jitterMs": jitter_ms,
                        "packetLoss": packet_loss,
                        "bitrateKbps": bitrate_kbps,
                        "mos": mos,
                        "codec": codec,
                        "candidateType": candidate_type,
                    }
                )
            )
        )

    # ── transfer ─────────────────────────────────────────────────────────────

    def transfer_blind(
        self,
        session_id: str,
        leg_id: str,
        target_type: str,
        *,
        target_address: Optional[str] = None,
        target_identity_id: Optional[str] = None,
        caller_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Hand the call off and drop ``leg_id`` immediately."""
        return self._dict(
            self._send(
                p.voice_transfer_blind(
                    session_id, leg_id, target_type, target_address, target_identity_id, caller_id
                )
            )
        )

    def transfer_warm_start(
        self,
        session_id: str,
        other_party_leg_id: str,
        target_type: str,
        *,
        target_address: Optional[str] = None,
        target_identity_id: Optional[str] = None,
        caller_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start a consult leg while the other party waits on hold."""
        return self._dict(
            self._send(
                p.voice_transfer_warm_start(
                    session_id, other_party_leg_id, target_type, target_address, target_identity_id, caller_id
                )
            )
        )

    def transfer_warm_complete(self, other_party_leg_id: str, leg_id: str) -> Dict[str, Any]:
        """Connect the parties and drop the initiator."""
        return self._dict(self._send(p.voice_transfer_warm_finish("complete", other_party_leg_id, leg_id)))

    def transfer_warm_cancel(self, other_party_leg_id: str, consult_leg_id: str) -> Dict[str, Any]:
        """Abandon the consult and return to the original party."""
        return self._dict(
            self._send(p.voice_transfer_warm_finish("cancel", other_party_leg_id, consult_leg_id))
        )

    # ── conference ───────────────────────────────────────────────────────────

    def mute(self, leg_id: str, muted: bool = True) -> Dict[str, Any]:
        """Mute or unmute a participant."""
        return self._dict(self._send(p.voice_conference_mute(leg_id, muted)))

    def remove(self, session_id: str, leg_id: str) -> Dict[str, Any]:
        """Remove a participant from the conference."""
        return self._dict(self._send(p.voice_conference_remove(session_id, leg_id)))

    # ── recordings ───────────────────────────────────────────────────────────

    def recording_download_url(self, access_token: str, *, inline: bool = False) -> str:
        """Build the download URL for a recording access token.

        There is **no permanent URL** for a recording: the console mints a
        short-lived HMAC token bound to the recording id and its expiry, and
        this turns that token into a fetchable link.
        """
        query = {"token": access_token}
        if inline:
            query["inline"] = "1"
        return "{0}/partner/voice/recordings/download?{1}".format(
            self._client.config.http_base, urlencode(query)
        )
