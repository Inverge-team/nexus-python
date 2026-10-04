import unittest

from nexus import NexusClient
from nexus._payloads import Request
from nexus.exceptions import (
    ApiError,
    AuthenticationError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TransportError,
    ValidationError,
)
from support import FakeTransport, make_client


class EngineTest(unittest.TestCase):
    def test_sends_auth_and_content_headers(self):
        client, transport = make_client()
        client.realtime.emit("room", "ping", {"n": 1})
        headers = transport.last["headers"]
        self.assertEqual(headers["x-api-key"], "nxs_test_secret")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertIn("inverge-nexus-python/", headers["User-Agent"])

    def test_default_headers_are_merged(self):
        client, transport = make_client(default_headers={"x-trace": "abc"})
        client.realtime.emit("room", "ping")
        self.assertEqual(transport.last["headers"]["x-trace"], "abc")

    def test_maps_status_to_exception_types(self):
        cases = [
            (400, ValidationError),
            (401, AuthenticationError),
            (404, NotFoundError),
            (422, ValidationError),
            (429, RateLimitError),
            (503, ServerError),
            (418, ApiError),
        ]
        for status, expected in cases:
            client, transport = make_client()
            transport.queue_error(status, "some_code", "boom")
            with self.assertRaises(expected) as caught:
                client.realtime.emit("room", "ping")
            self.assertEqual(caught.exception.status, status)
            self.assertEqual(caught.exception.code, "some_code")
            self.assertEqual(caught.exception.message, "boom")

    def test_silent_mode_swallows_and_returns_empty(self):
        client, transport = make_client(silent=True)
        transport.queue_error(500, "internal_error", "down")
        self.assertEqual(client.realtime.emit("room", "ping"), {})

    def test_retries_safe_requests_only(self):
        client, transport = make_client(max_retries=2, retry_backoff=0.0)
        transport.queue_error(503).queue_error(503).queue({"written": 1})
        self.assertEqual(client.events.batch([{"name": "x"}]), 1)
        self.assertEqual(len(transport.requests), 3)

    def test_does_not_retry_unsafe_requests(self):
        client, transport = make_client(max_retries=3, retry_backoff=0.0)
        transport.queue_error(503)
        with self.assertRaises(ServerError):
            client.push.send(["u"], title="hi")
        self.assertEqual(len(transport.requests), 1)

    def test_transport_errors_propagate(self):
        client, transport = make_client()
        transport.queue_exception(TransportError("dns"))
        with self.assertRaises(TransportError):
            client.realtime.emit("room", "ping")

    def test_304_is_not_modified(self):
        client, transport = make_client()
        transport.queue(b"", 304)
        result = client.request(Request("POST", "/partner/remote-config/fetch", {}))
        self.assertEqual(result, {"notModified": True})

    def test_request_after_close_is_a_noop(self):
        client, transport = make_client()
        client.close()
        self.assertIsNone(client.request(Request("GET", "/partner/rooms")))
        self.assertEqual(transport.requests, [])

    def test_client_accepts_a_config_object(self):
        from nexus import NexusConfig

        transport = FakeTransport()
        client = NexusClient(NexusConfig("nxs_k", base_url="http://a", batch=False), transport=transport)
        self.assertEqual(client.config.base_url, "http://a")


if __name__ == "__main__":
    unittest.main()
