"""The end user's IP reaching Messaging's per-IP rate limit — from a request,
and from a Celery task the request queued (the request is gone by then)."""

import sys
import types
import unittest

from nexus import client_ip as cip
from nexus.integrations.wsgi import NexusWSGIMiddleware
from support import make_client
from test_async import make_async_client, run

USER = "8.8.4.4"  # Python counts the documentation ranges as private


class ClientIpTest(unittest.TestCase):
    def tearDown(self):
        cip._forwarding = None

    def test_a_verification_forwards_the_bound_address(self):
        client, t = make_client()
        with cip.client_ip(USER):
            client.messaging.verifications.start("+9647501234567")
        self.assertEqual(t.last["headers"]["X-Client-IP"], USER)
        self.assertNotIn("clientIp", t.last["json"])

    def test_an_explicit_address_goes_in_the_body_and_wins(self):
        client, t = make_client()
        with cip.client_ip(USER):
            client.messaging.verifications.start("+9647501234567", client_ip="1.1.1.1")
        self.assertEqual(t.last["json"]["clientIp"], "1.1.1.1")
        self.assertNotIn("X-Client-IP", t.last["headers"])

    def test_only_otp_sends_pick_the_address_up_by_themselves(self):
        client, t = make_client()
        with cip.client_ip(USER):
            client.messaging.send("sms", "+9647501234567", body="Your order shipped")
            self.assertNotIn("X-Client-IP", t.last["headers"])
            client.messaging.send("sms", "+9647501234567", body="Code 1234", message_class="otp")
            self.assertEqual(t.last["headers"]["X-Client-IP"], USER)

    def test_the_async_client_forwards_it_too(self):
        client, t = make_async_client()

        async def go():
            with cip.client_ip(USER):
                await client.messaging.verifications.start("+9647501234567")

        run(go())
        self.assertEqual(t.inner.last["headers"]["X-Client-IP"], USER)

    def test_proxy_and_private_addresses_are_filtered(self):
        for ip in ["10.1.2.3", "172.20.0.1", "192.168.0.4", "127.0.0.1", "100.64.0.1", "::1", "fd12::1", "fe80::1", "::ffff:10.0.0.1", "nope", None]:
            self.assertIsNone(cip.public_ip(ip), ip)
        self.assertEqual(cip.public_ip("::ffff:8.8.4.4"), "8.8.4.4")
        self.assertEqual(cip.public_ip("2a00:1450:4001:81b::200e"), "2a00:1450:4001:81b::200e")

    def test_forwarding_can_be_switched_off(self):
        client, t = make_client()
        cip.set_forwarding(False)
        with cip.client_ip(USER):
            client.messaging.verifications.start("+9647501234567")
        self.assertNotIn("X-Client-IP", t.last["headers"])

    def test_the_middleware_binds_remote_addr_never_x_forwarded_for(self):
        client, _ = make_client()
        seen = {}

        def inner(environ, start_response):
            seen["ip"] = cip.current_client_ip()
            start_response("200 OK", [])
            return [b"ok"]

        app = NexusWSGIMiddleware(inner, client)
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/otp",
            "REMOTE_ADDR": USER,
            "HTTP_X_FORWARDED_FOR": "9.9.9.9",  # anyone can send this
            "wsgi.url_scheme": "https",
            "HTTP_HOST": "api.example.com",
        }
        list(app(environ, lambda *a, **k: None))
        self.assertEqual(seen["ip"], USER)
        self.assertIsNone(cip.current_client_ip(), "nothing leaks past the request")


class CeleryTest(unittest.TestCase):
    """A request queues a task; the worker starts the verification."""

    def setUp(self):
        signals = types.SimpleNamespace(**{name: _Signal() for name in (
            "before_task_publish", "task_prerun", "task_postrun", "task_failure", "worker_shutdown")})
        self._saved = sys.modules.get("celery")
        sys.modules["celery"] = types.SimpleNamespace(signals=signals)
        self.signals = signals
        from nexus.integrations import celery as integration

        integration._client_ip_installed = False
        integration.propagate_client_ip()

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("celery", None)
        else:
            sys.modules["celery"] = self._saved

    def test_the_task_forwards_the_requesters_address(self):
        headers = {}
        with cip.client_ip(USER):  # the web request, where the middleware bound it
            self.signals.before_task_publish.send(headers=headers)
        self.assertEqual(headers["nexus_client_ip"], USER)

        client, t = make_client()
        task = types.SimpleNamespace(request=types.SimpleNamespace(headers=headers))
        self.signals.task_prerun.send(task_id="t1", task=task)
        client.messaging.verifications.start("+9647501234567", channels=["whatsapp"])
        self.signals.task_postrun.send(task_id="t1", task=task)

        self.assertEqual(t.last["headers"]["X-Client-IP"], USER)
        self.assertIsNone(cip.current_client_ip())

    def test_a_task_queued_without_a_request_carries_nothing(self):
        headers = {}
        self.signals.before_task_publish.send(headers=headers)
        self.assertNotIn("nexus_client_ip", headers)


class _Signal:
    def __init__(self):
        self.receivers = []

    def connect(self, fn, weak=True):
        self.receivers.append(fn)
        return fn

    def send(self, **kwargs):
        for fn in self.receivers:
            fn(**kwargs)


if __name__ == "__main__":
    unittest.main()
