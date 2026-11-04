"""Messaging — send on WhatsApp, Telegram, SMS and the other channels.

Send-only: an API key sends messages, reads a sent message's status and
delivery timeline, uploads a file to attach, and runs OTP verifications.
Templates, numbers, senders, webhooks, campaigns and the inbox are managed in
the console.

``content`` is passed through as given; the API validates it per channel
(WhatsApp keys it by ``kind``, Telegram by ``type``) and its error names the
field that is wrong.
"""

from __future__ import annotations

import mimetypes
import os
import uuid
from datetime import datetime
from typing import IO, Any, Dict, Mapping, Optional, Sequence, Tuple, Union

from .. import _payloads as p
from .base import Resource

__all__ = ["Messaging", "Verifications", "message_fields", "verification_fields", "read_file", "new_idempotency_key"]

#: Bytes, a path, or an open binary file.
FileInput = Union[bytes, bytearray, str, "os.PathLike[str]", IO[bytes]]


def message_fields(
    channel: str,
    to: str,
    *,
    body: Optional[str] = None,
    content: Optional[Mapping[str, Any]] = None,
    sender: Optional[str] = None,
    template: Optional[str] = None,
    language: Optional[str] = None,
    variables: Optional[Mapping[str, Any]] = None,
    message_class: Optional[str] = None,
    scheduled_at: Union[datetime, str, None] = None,
    validity_period_sec: Optional[int] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    track_links: Optional[bool] = None,
    client_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """The send body, in the API's field names."""
    if not channel:
        raise ValueError("messaging.send() requires a channel, e.g. 'whatsapp' or 'telegram'.")
    if not to:
        raise ValueError("messaging.send() requires a recipient (to=).")
    if body is None and content is None and template is None:
        raise ValueError("messaging.send() needs body=, content= or template=.")
    if isinstance(scheduled_at, datetime):
        scheduled_at = scheduled_at.isoformat()
    return {
        "channel": channel,
        "to": to,
        "body": body,
        "content": dict(content) if content is not None else None,
        "sender": sender,
        "templateKey": template,
        "templateLocale": language,
        "templateVariables": {k: str(v) for k, v in variables.items()} if variables else None,
        "messageClass": message_class,
        "scheduledAt": scheduled_at,
        "validityPeriodSec": validity_period_sec,
        "metadata": dict(metadata) if metadata is not None else None,
        "trackLinks": track_links,
        "clientIp": client_ip,
    }


def verification_fields(
    to: str,
    *,
    channels: Optional[Sequence[str]] = None,
    whatsapp_template: Optional[str] = None,
    whatsapp_language: Optional[str] = None,
    code_length: Optional[int] = None,
    ttl: Optional[int] = None,
    sms_fallback: bool = False,
    fallback_after: Optional[int] = None,
    sms_sender: Optional[str] = None,
    sms_template: Optional[str] = None,
    sender_username: Optional[str] = None,
    max_attempts: Optional[int] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if not to:
        raise ValueError("verifications.start() requires a phone number in E.164 form.")
    return {
        "to": to,
        "channels": list(channels) if channels else None,
        "whatsappTemplate": whatsapp_template,
        "whatsappLanguage": whatsapp_language,
        "codeLength": code_length,
        "ttl": ttl,
        "fallback": "sms" if sms_fallback else None,
        "fallbackAfter": fallback_after,
        "smsSender": sms_sender,
        "smsTemplate": sms_template,
        "senderUsername": sender_username,
        "maxAttempts": max_attempts,
        "metadata": dict(metadata) if metadata is not None else None,
    }


def read_file(
    file: FileInput, content_type: Optional[str], filename: Optional[str]
) -> Tuple[bytes, str, Optional[str]]:
    """Bytes, media type and filename for an upload. A path fills in the last two."""
    if isinstance(file, (bytes, bytearray)):
        data = bytes(file)
    elif isinstance(file, (str, os.PathLike)):
        path = os.fspath(file)
        with open(path, "rb") as fh:
            data = fh.read()
        filename = filename or os.path.basename(path)
    else:
        data = file.read()
        name = getattr(file, "name", None)
        if not filename and isinstance(name, str):
            filename = os.path.basename(name)
    if not data:
        raise ValueError("The file is empty.")
    if not content_type and filename:
        content_type = mimetypes.guess_type(filename)[0]
    if not content_type:
        raise ValueError("Pass content_type= (e.g. 'image/jpeg'); it could not be guessed.")
    return data, content_type, filename


def new_idempotency_key(key: Optional[str] = None) -> str:
    return key or "nxs-" + uuid.uuid4().hex


class _Direct(Resource):
    # Messaging bypasses a background/queued dispatcher (the caller needs the
    # message id back, and a queue cannot carry an upload's raw body) and
    # raises even when the client is ``silent`` — a failed send must not look
    # like a sent one. A switched-off service still returns None.
    def _send(self, request: "p.Request") -> Any:
        return self._client.request(request, direct=True)


class Verifications(_Direct):
    """``client.messaging.verifications`` — one-time codes on WhatsApp or Telegram, cheapest first."""

    def start(self, to: str, **options: Any) -> Optional[Dict[str, Any]]:
        """Send a code to ``to`` (E.164). Keep the returned ``id`` to check it.

        The code goes out on whichever of WhatsApp and Telegram is cheaper for
        the number, and moves to the other when that one refuses it or hasn't
        delivered it within ``fallback_after`` seconds. ``channels=[...]`` sets
        the order yourself.

        ::

            v = client.messaging.verifications.start("+9647501234567")
        """
        return self._send(p.verification_start(verification_fields(to, **options)))

    def check(self, verification_id: str, code: str) -> Optional[Dict[str, Any]]:
        """Check what the user typed. ``valid`` says whether it matched.

        ::

            if client.messaging.verifications.check(v["id"], "123456")["valid"]:
                ...
        """
        return self._send(p.verification_check(verification_id, code))

    def get(self, verification_id: str) -> Optional[Dict[str, Any]]:
        return self._send(p.verification_get(verification_id))

    def cancel(self, verification_id: str) -> Optional[Dict[str, Any]]:
        return self._send(p.verification_cancel(verification_id))


class Messaging(_Direct):
    """``client.messaging``"""

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.verifications = Verifications(client)

    def send(
        self,
        channel: str,
        to: str,
        *,
        idempotency_key: Optional[str] = None,
        **fields: Any,
    ) -> Optional[Dict[str, Any]]:
        """Send a message. Returns ``{"id", "status", "price", …}`` once it is queued.

        ::

            client.messaging.send("whatsapp", "+9647501234567",
                                  template="order_shipped", language="en_US",
                                  variables={"order_id": "A-91"})
            client.messaging.send("telegram", "@alice", body="Your order shipped")

        Safe to retry: every send carries an idempotency key (pass your own to
        dedupe across processes).
        """
        body = message_fields(channel, to, **fields)
        return self._send(p.messaging_send(body, new_idempotency_key(idempotency_key)))

    def get(self, message_id: str) -> Optional[Dict[str, Any]]:
        """A sent message's current status, price and timestamps."""
        return self._send(p.messaging_get(message_id))

    def events(self, message_id: str) -> list:
        """The message's delivery timeline: queued → submitted → sent → delivered."""
        data = self._send(p.messaging_events(message_id))
        events = data.get("events") if isinstance(data, Mapping) else None
        return list(events) if isinstance(events, list) else []

    def upload_media(
        self, file: FileInput, *, content_type: Optional[str] = None, filename: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Upload a file for WhatsApp; send it as ``{"id": result["mediaId"]}``."""
        return self._send(p.messaging_upload_media(*read_file(file, content_type, filename)))

    def upload_telegram_file(
        self, file: FileInput, *, content_type: Optional[str] = None, filename: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Upload a file for Telegram; send ``result["uploadId"]`` as the media."""
        return self._send(p.messaging_upload_telegram(*read_file(file, content_type, filename)))
