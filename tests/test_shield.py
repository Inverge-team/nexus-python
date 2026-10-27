import asyncio
import hashlib
import base64
import unittest

from support import make_client

VERDICT = {
    "valid": True,
    "decision": "allow",
    "mode": "enforce",
    "reason": None,
    "resource": "checkout",
    "policy": "checkout",
    "token": {"score": 91, "level": "strong", "providers": ["key_attestation"], "deviceId": "d1", "appId": "a1", "platform": "android", "expiresAt": "x"},
}


class ShieldTest(unittest.TestCase):
    def test_verify_request_reads_headers_and_hashes_the_body(self):
        client, transport = make_client()
        transport.queue(VERDICT)
        v = client.shield.verify_request(
            headers={"X-Nexus-Shield": "DPoP tok", "dpop": "proof"},
            method="post",
            url="https://api.example.com/checkout",
            body=b'{"total":42}',
            resource="checkout",
        )
        self.assertTrue(v.allowed)
        self.assertEqual((v.score, v.level, v.device_id), (91, "strong", "d1"))
        req = transport.last
        self.assertEqual(req["path"], "partner/shield/verify")
        expected_bh = base64.urlsafe_b64encode(hashlib.sha256(b'{"total":42}').digest()).rstrip(b"=").decode()
        self.assertEqual(
            req["json"],
            {"token": "tok", "proof": "proof", "method": "POST", "url": "https://api.example.com/checkout", "resource": "checkout", "bodyHash": expected_bh},
        )

    def test_missing_token_never_calls_the_api(self):
        client, transport = make_client()
        v = client.shield.verify(None, None, "GET", "https://a/b")
        self.assertFalse(v.allowed)
        self.assertEqual(v.reason, "token_missing")
        self.assertEqual(transport.requests, [])

    def test_denied_verdict(self):
        client, transport = make_client()
        transport.queue({**VERDICT, "valid": False, "decision": "deny", "reason": "proof_replayed", "token": None})
        v = client.shield.verify("t", "p", "POST", "https://a/b", "checkout")
        self.assertFalse(v.allowed)
        self.assertEqual(v.reason, "proof_replayed")


if __name__ == "__main__":
    unittest.main()
