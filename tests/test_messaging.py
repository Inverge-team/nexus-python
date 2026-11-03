import os
import tempfile
import unittest

from nexus.exceptions import ValidationError
from support import make_client
from test_async import make_async_client, run


class MessagingSendTest(unittest.TestCase):
    def test_whatsapp_template_by_named_variables(self):
        client, transport = make_client()
        transport.queue({"id": "msg_1", "status": "queued"}, 202)
        out = client.messaging.send(
            "whatsapp", "+9647501234567", template="order_shipped", language="en_US", variables={"order_id": "A-91", "n": 3}
        )
        self.assertEqual(out["id"], "msg_1")
        req = transport.last
        self.assertEqual((req["method"], req["path"]), ("POST", "partner/messaging/messages"))
        self.assertEqual(
            req["json"],
            {
                "channel": "whatsapp",
                "to": "+9647501234567",
                "templateKey": "order_shipped",
                "templateLocale": "en_US",
                "templateVariables": {"order_id": "A-91", "n": "3"},
            },
        )

    def test_structured_content_passes_through_untouched(self):
        client, transport = make_client()
        content = {"type": "photo", "media": "tgf_abcdefghijkl", "caption": "Hi", "replyMarkup": {"inline_keyboard": []}}
        client.messaging.send("telegram", "@alice", content=content, metadata={"order": 7}, track_links=False)
        body = transport.last["json"]
        self.assertEqual(body["content"], content)
        self.assertEqual(body["metadata"], {"order": 7})
        self.assertIs(body["trackLinks"], False)
        self.assertNotIn("templateKey", body)  # nulls are dropped

    def test_every_send_carries_an_idempotency_key(self):
        client, transport = make_client()
        client.messaging.send("telegram", "@a", body="x")
        client.messaging.send("telegram", "@a", body="x")
        keys = [r["headers"]["Idempotency-Key"] for r in transport.requests]
        self.assertTrue(all(k.startswith("nxs-") for k in keys))
        self.assertNotEqual(keys[0], keys[1])

    def test_a_given_idempotency_key_is_used(self):
        client, transport = make_client()
        client.messaging.send("whatsapp", "+1", body="x", idempotency_key="order-91")
        self.assertEqual(transport.last["headers"]["Idempotency-Key"], "order-91")

    def test_send_is_retried_with_the_same_key(self):
        # Safe only because the key is the same on both attempts.
        client, transport = make_client(max_retries=1, retry_backoff=0)
        transport.queue_error(503, "unavailable")
        transport.queue({"id": "msg_2", "status": "queued"}, 202)
        self.assertEqual(client.messaging.send("whatsapp", "+1", body="x")["id"], "msg_2")
        k1, k2 = (r["headers"]["Idempotency-Key"] for r in transport.requests)
        self.assertEqual(k1, k2)

    def test_scheduled_at_accepts_a_datetime(self):
        from datetime import datetime, timezone

        client, transport = make_client()
        client.messaging.send("sms", "+1", body="x", scheduled_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc))
        self.assertEqual(transport.last["json"]["scheduledAt"], "2026-01-02T03:04:00+00:00")

    def test_refuses_a_send_with_nothing_to_send(self):
        client, _ = make_client()
        with self.assertRaises(ValueError):
            client.messaging.send("whatsapp", "+1")

    def test_a_rejection_raises_with_the_api_code(self):
        client, transport = make_client()
        transport.queue_error(400, "content_rejected", 'Template "x" expects 2 parameter(s); 1 supplied.')
        with self.assertRaises(ValidationError) as ctx:
            client.messaging.send("whatsapp", "+1", template="x")
        self.assertIn("expects 2", str(ctx.exception))

    def test_raises_even_when_silent(self):
        client, transport = make_client(silent=True)
        transport.queue_error(402, "insufficient_balance", "Top up your wallet.")
        with self.assertRaises(Exception) as ctx:
            client.messaging.send("whatsapp", "+1", body="x")
        self.assertEqual(getattr(ctx.exception, "code", None), "insufficient_balance")

    def test_bypasses_a_background_dispatcher(self):
        # The caller needs the message id; a queue would return None.
        client, transport = make_client()
        bg = client.background()
        transport.queue({"id": "msg_3"}, 202)
        self.assertEqual(bg.messaging.send("telegram", "@a", body="x")["id"], "msg_3")
        bg.close()


class MessagingReadTest(unittest.TestCase):
    def test_get_and_events(self):
        client, transport = make_client()
        transport.queue({"id": "msg 1", "status": "delivered"})
        transport.queue({"events": [{"type": "delivered"}]})
        self.assertEqual(client.messaging.get("msg 1")["status"], "delivered")
        self.assertEqual(client.messaging.events("msg 1"), [{"type": "delivered"}])
        self.assertEqual(transport.paths(), ["/partner/messaging/messages/msg%201", "/partner/messaging/messages/msg%201/events"])


class MessagingUploadTest(unittest.TestCase):
    def test_whatsapp_media_is_a_raw_body(self):
        client, transport = make_client()
        transport.queue({"mediaId": "123"}, 201)
        out = client.messaging.upload_media(b"%PDF-1.7", content_type="application/pdf", filename="invoice 1.pdf")
        self.assertEqual(out["mediaId"], "123")
        req = transport.last
        self.assertEqual(req["path"], "partner/messaging/media?filename=invoice%201.pdf")
        self.assertEqual(req["headers"]["Content-Type"], "application/pdf")
        self.assertEqual(req["body"], b"%PDF-1.7")

    def test_telegram_upload_from_a_path_guesses_type_and_name(self):
        client, transport = make_client()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "photo.png")
            with open(path, "wb") as fh:
                fh.write(b"\x89PNG")
            client.messaging.upload_telegram_file(path)
        req = transport.last
        self.assertEqual(req["path"], "partner/messaging/telegram/uploads?filename=photo.png")
        self.assertEqual(req["headers"]["Content-Type"], "image/png")
        self.assertEqual(req["body"], b"\x89PNG")

    def test_unknown_type_must_be_given(self):
        client, _ = make_client()
        with self.assertRaises(ValueError):
            client.messaging.upload_media(b"data")


class VerificationsTest(unittest.TestCase):
    def test_start_check_cancel(self):
        client, transport = make_client()
        transport.queue({"id": "ver_1", "status": "pending"}, 201)
        transport.queue({"id": "ver_1", "valid": True, "attemptsRemaining": 4})
        v = client.messaging.verifications.start("+9647501234567", code_length=6, sms_fallback=True, ttl=300)
        self.assertTrue(client.messaging.verifications.check(v["id"], "123456")["valid"])
        client.messaging.verifications.cancel("ver_1")
        start, check, cancel = transport.requests
        self.assertEqual(start["json"], {"to": "+9647501234567", "codeLength": 6, "ttl": 300, "fallback": "sms"})
        self.assertEqual((check["path"], check["json"]), ("partner/messaging/verifications/ver_1/check", {"code": "123456"}))
        self.assertEqual(cancel["path"], "partner/messaging/verifications/ver_1/cancel")

    def test_start_with_a_channel_plan_and_template(self):
        client, transport = make_client()
        client.messaging.verifications.start("+9647501234567", channels=("telegram", "whatsapp"), whatsapp_template="acme_login", whatsapp_language="ar")
        self.assertEqual(
            transport.last["json"],
            {"to": "+9647501234567", "channels": ["telegram", "whatsapp"], "whatsappTemplate": "acme_login", "whatsappLanguage": "ar"},
        )

    def test_start_and_check_are_never_retried(self):
        # A retried start sends a second code; a retried check spends an attempt.
        client, transport = make_client(max_retries=3, retry_backoff=0)
        transport.queue_error(503)
        with self.assertRaises(Exception):
            client.messaging.verifications.start("+1")
        transport.queue_error(503)
        with self.assertRaises(Exception):
            client.messaging.verifications.check("ver_1", "1234")
        self.assertEqual(len(transport.requests), 2)


class MessagingSwitchTest(unittest.TestCase):
    def test_switched_off_makes_no_call(self):
        client, transport = make_client(services={"messaging": False})
        self.assertIsNone(client.messaging.send("whatsapp", "+1", body="x"))
        self.assertIsNone(client.messaging.verifications.start("+1"))
        self.assertEqual(transport.requests, [])


class AsyncMessagingTest(unittest.TestCase):
    def test_async_send_and_upload_match_sync(self):
        async def scenario():
            client, transport = make_async_client()
            await client.messaging.send("whatsapp", "+1", template="t", variables={"a": "b"}, idempotency_key="k")
            await client.messaging.upload_media(b"x", content_type="image/jpeg")
            await client.messaging.verifications.check("v", "1234")
            await client.aclose()
            return transport.requests

        sync_client, sync_transport = make_client()
        sync_client.messaging.send("whatsapp", "+1", template="t", variables={"a": "b"}, idempotency_key="k")
        sync_client.messaging.upload_media(b"x", content_type="image/jpeg")
        sync_client.messaging.verifications.check("v", "1234")
        strip = lambda rs: [(r["method"], r["path"], r["body"], r["headers"].get("Idempotency-Key"), r["headers"].get("Content-Type")) for r in rs]
        self.assertEqual(strip(run(scenario())), strip(sync_transport.requests))


if __name__ == "__main__":
    unittest.main()
