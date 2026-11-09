import hashlib
import hmac
import time
import unittest

from nexus.resources.push_auth import verify_webhook
from support import make_client
from test_async import make_async_client, run

PENDING = {"id": "pac_1", "status": "pending", "stage": None, "method": "number_match", "requesterView": {"number": "42"}}


class PushAuthTest(unittest.TestCase):
    def test_create_challenge_sends_secret_and_wire_fields(self):
        client, transport = make_client()
        transport.queue(PENDING, 201)
        out = client.push_auth.create_challenge(
            "user_1", "Sign in to Acme", method="number_match",
            fields=[{"label": "Amount", "value": "25"}], context={"device": "Chrome"}, fallback_to="+9647501234567",
        )
        self.assertEqual(out["requesterView"]["number"], "42")
        req = transport.last
        self.assertEqual((req["method"], req["path"]), ("POST", "partner/push-auth/challenges"))
        self.assertNotIn("X-Nexus-Secret", req["headers"])
        self.assertEqual(
            req["json"],
            {
                "identity": "user_1",
                "method": "number_match",
                "details": {"message": "Sign in to Acme", "fields": [{"label": "Amount", "value": "25"}], "context": {"device": "Chrome"}},
                "fallback": {"to": "+9647501234567"},
            },
        )

    def test_wait_long_polls_with_a_longer_timeout(self):
        client, transport = make_client()
        transport.queue(PENDING).queue(PENDING).queue({**PENDING, "status": "approved", "receipt": "a.b.c"})
        done = client.push_auth.wait_for_challenge("pac_1", timeout_seconds=60)
        self.assertEqual(done["status"], "approved")
        poll = transport.requests[1]
        self.assertTrue(poll["path"].startswith("partner/push-auth/challenges/pac_1?wait="))
        self.assertGreater(poll["timeout"], 25)

    def test_until_stage_stops_at_awaiting_code(self):
        client, transport = make_client()
        transport.queue(PENDING).queue({**PENDING, "stage": "awaiting_code"})
        c = client.push_auth.wait_for_challenge("pac_1", until_stage="awaiting_code")
        self.assertEqual(c["stage"], "awaiting_code")
        self.assertEqual(len(transport.requests), 2)

    def test_factor_actions_and_receipts(self):
        client, transport = make_client()
        client.push_auth.revoke_factor("paf_1")
        client.push_auth.lock_factor("paf_1")
        client.push_auth.verify_receipt("t", consume=False)
        client.push_auth.complete("pac_1", "123456")
        self.assertEqual(
            [(r["method"], r["path"]) for r in transport.requests],
            [
                ("DELETE", "partner/push-auth/factors/paf_1"),
                ("POST", "partner/push-auth/factors/paf_1/lock"),
                ("POST", "partner/push-auth/tokens/verify"),
                ("POST", "partner/push-auth/challenges/pac_1/complete"),
            ],
        )
        self.assertEqual(transport.requests[2]["json"], {"token": "t", "consume": False})

    def test_webhook_signature(self):
        body = b'{"id":"pawh_1"}'
        ts = int(time.time())
        sig = hmac.new(b"whsec_x", b"%d.n1." % ts + body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook(body, sig, ts, "n1", "whsec_x"))
        self.assertFalse(verify_webhook(body + b" ", sig, ts, "n1", "whsec_x"))
        self.assertFalse(verify_webhook(body, sig, ts - 1000, "n1", "whsec_x"))

    def test_async(self):
        client, transport = make_async_client()
        transport.queue(PENDING, 201)
        out = run(client.push_auth.create_challenge("user_1", "Sign in", method="tap"))
        self.assertEqual(out["id"], "pac_1")


if __name__ == "__main__":
    unittest.main()
