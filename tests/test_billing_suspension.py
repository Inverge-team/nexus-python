import unittest

from nexus import _suspension
from nexus.exceptions import ApiError, BillingSuspendedError, ServerError, TransportError, ValidationError, is_delivery_failure
from support import make_client

SUSPENDED = {
    "error": {
        "code": "billing_suspended",
        "message": "API access is suspended: USD 54.56 is past due on invoice INV-2026-10-0001.",
        "details": {"retryAfterSeconds": 300},
    }
}


class BillingSuspensionTest(unittest.TestCase):
    def setUp(self):
        _suspension.reset()

    def tearDown(self):
        _suspension.reset()

    def test_one_402_stops_every_further_request(self):
        client, transport = make_client()
        transport.queue(SUSPENDED, 402)
        with self.assertRaises(BillingSuspendedError) as first:
            client.push.send(["u1"], title="Hi", body="there")
        self.assertFalse(first.exception.local)
        self.assertEqual(first.exception.retry_after, 300)

        for _ in range(50):
            with self.assertRaises(BillingSuspendedError) as again:
                client.push.send(["u1"], title="Hi", body="there")
            self.assertTrue(again.exception.local)
        self.assertEqual(len(transport.requests), 1)

    def test_telemetry_is_dropped_quietly(self):
        client, transport = make_client()
        transport.queue(SUSPENDED, 402)
        client.errors.capture("trip it")
        client.errors.capture("held back")
        client.events.track("signup")
        self.assertEqual(len(transport.requests), 1)

    def test_the_pause_is_shared_by_clients_in_the_process(self):
        a, ta = make_client()
        ta.queue(SUSPENDED, 402)
        a.errors.capture("trip it")
        b, tb = make_client()
        b.errors.capture("other client")
        self.assertEqual(len(tb.requests), 0)

    def test_after_the_pause_one_probe_and_success_restores_access(self):
        client, transport = make_client()
        transport.queue(SUSPENDED, 402)
        client.errors.capture("trip it")
        key = client.engine._suspension
        _suspension._pauses[key] = 0  # the pause ran out
        client.errors.capture("probe")
        client.errors.capture("normal")
        self.assertEqual(len(transport.requests), 3)
        self.assertNotIn(key, _suspension._pauses)

    def test_messaging_is_never_held_back_and_an_empty_wallet_is_not_a_suspension(self):
        client, transport = make_client()
        transport.queue(SUSPENDED, 402)
        client.errors.capture("trip it")
        transport.queue({"error": {"code": "insufficient_balance", "message": "Top up"}}, 402)
        with self.assertRaises(ApiError) as e:
            client.messaging.send("sms", "+9647501234567", body="x")
        self.assertNotIsInstance(e.exception, BillingSuspendedError)
        self.assertEqual(len(transport.requests), 2)

    def test_delivery_failures_are_never_reported(self):
        self.assertTrue(is_delivery_failure(BillingSuspendedError("x", status=402, code="billing_suspended")))
        self.assertTrue(is_delivery_failure(ServerError("x", status=503)))
        self.assertTrue(is_delivery_failure(TransportError("timeout")))
        self.assertFalse(is_delivery_failure(ValidationError("bad", status=422)))
        self.assertFalse(is_delivery_failure(ValueError("app bug")))

        client, transport = make_client()
        self.assertEqual(client.errors.capture_exception(ServerError("down", status=503)), {})
        self.assertEqual(len(transport.requests), 0)

    def test_old_queued_suspension_reports_are_recognised(self):
        self.assertTrue(_suspension.is_suspension_report("/partner/errors", {"message": "API access is suspended: USD 1"}))
        self.assertFalse(_suspension.is_suspension_report("/partner/errors", {"message": "boom"}))
        self.assertFalse(_suspension.is_suspension_report("/partner/events", {"message": "API access is suspended"}))


if __name__ == "__main__":
    unittest.main()
