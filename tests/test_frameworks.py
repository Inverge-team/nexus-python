"""Integration tests that run only when the framework is installed."""

import unittest

from support import FakeTransport


def _client(transport=None):
    from nexus import NexusClient

    transport = transport or FakeTransport()
    client = NexusClient(
        "nxs_test_secret", base_url="https://api.test", batch=False, transport=transport
    )
    return client, transport


try:
    import flask  # noqa: F401

    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False

try:
    import fastapi  # noqa: F401

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

try:
    import django  # noqa: F401

    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False


@unittest.skipUnless(HAS_FLASK, "flask is not installed")
class FlaskTest(unittest.TestCase):
    def _app(self, **config):
        from flask import Flask

        from nexus.integrations.flask import NexusFlask

        app = Flask(__name__)
        app.config.update(TESTING=True, **config)
        client, transport = _client()
        extension = NexusFlask(app, client)

        @app.get("/orders/<int:order_id>")
        def show(order_id):
            return {"id": order_id}

        @app.get("/boom")
        def boom():
            raise RuntimeError("kaput")

        return app, transport, extension

    def test_tracks_requests_with_the_route_template(self):
        app, transport, _ = self._app()
        with app.test_client() as test_client:
            self.assertEqual(test_client.get("/orders/42").status_code, 200)
        event = transport.last["json"]["events"][0]
        self.assertEqual(event["name"], "http_request")
        self.assertEqual(event["properties"]["path"], "/orders/<int:order_id>")
        self.assertEqual(event["properties"]["status"], 200)

    def test_binds_the_distinct_id_header(self):
        app, transport, _ = self._app()
        with app.test_client() as test_client:
            test_client.get("/orders/1", headers={"X-Nexus-Distinct-Id": "u_7"})
        self.assertEqual(transport.last["json"]["distinctId"], "u_7")

    def test_reports_unhandled_exceptions(self):
        app, transport, _ = self._app()
        with app.test_client() as test_client:
            try:
                test_client.get("/boom")
            except RuntimeError:
                pass
        errors = [r for r in transport.requests if r["path"] == "partner/errors"]
        self.assertTrue(errors)
        self.assertEqual(errors[0]["json"]["type"], "RuntimeError")
        self.assertFalse(errors[0]["json"]["handled"])


@unittest.skipUnless(HAS_FASTAPI, "fastapi is not installed")
class FastApiTest(unittest.TestCase):
    def _app(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from nexus.integrations.fastapi import NexusMiddleware

        client, transport = _client()
        app = FastAPI()
        app.add_middleware(NexusMiddleware, client=client)

        @app.get("/orders/{order_id}")
        async def show(order_id: int):
            return {"id": order_id}

        @app.get("/boom")
        async def boom():
            raise RuntimeError("kaput")

        return TestClient(app, raise_server_exceptions=False), transport

    def test_tracks_requests_with_the_route_template(self):
        test_client, transport = self._app()
        self.assertEqual(test_client.get("/orders/42").status_code, 200)
        event = transport.last["json"]["events"][0]
        self.assertEqual(event["properties"]["path"], "/orders/{order_id}")
        self.assertEqual(event["properties"]["status"], 200)

    def test_reports_unhandled_exceptions(self):
        test_client, transport = self._app()
        test_client.get("/boom")
        self.assertIn("/partner/errors", transport.paths())


@unittest.skipUnless(HAS_DJANGO, "django is not installed")
class DjangoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import django
        from django.conf import settings

        if not settings.configured:
            settings.configure(
                DEBUG=False,
                ALLOWED_HOSTS=["*"],
                SECRET_KEY="test",
                ROOT_URLCONF="django_urls_for_tests",
                INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
                MIDDLEWARE=["nexus.integrations.django.NexusMiddleware"],
                DATABASES={},
                NEXUS={"API_KEY": "nxs_test_secret", "BASE_URL": "https://api.test", "BATCH": False},
            )
            django.setup()

    def _request_factory(self):
        from django.test import RequestFactory

        return RequestFactory()

    def test_middleware_tracks_and_reports(self):
        from nexus.integrations.django.middleware import NexusMiddleware

        transport = FakeTransport()
        # The middleware builds its own client from settings; swap the transport
        # on the engine it created so we can assert on the traffic.
        middleware = NexusMiddleware(lambda request: _FakeResponse(200))
        middleware.client.engine.transport = transport

        request = self._request_factory().get("/orders/42", HTTP_X_NEXUS_DISTINCT_ID="u_3")
        response = middleware(request)
        self.assertEqual(response.status_code, 200)
        event = transport.last["json"]["events"][0]
        self.assertEqual(event["properties"]["method"], "GET")
        self.assertEqual(event["properties"]["status"], 200)
        self.assertEqual(transport.last["json"]["distinctId"], "u_3")

    def test_middleware_reports_view_exceptions(self):
        from nexus.integrations.django.middleware import NexusMiddleware

        transport = FakeTransport()
        middleware = NexusMiddleware(lambda request: _FakeResponse(200))
        middleware.client.engine.transport = transport

        request = self._request_factory().get("/boom")
        middleware.process_exception(request, ValueError("bad"))
        self.assertEqual(transport.last["path"], "partner/errors")
        self.assertEqual(transport.last["json"]["type"], "ValueError")

    def test_settings_reader_merges_both_spellings(self):
        from nexus.integrations.django.conf import client_options, tracker_options

        self.assertEqual(client_options()["api_key"], "nxs_test_secret")
        self.assertFalse(client_options()["batch"])
        self.assertTrue(tracker_options()["track_requests"])


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


if __name__ == "__main__":
    unittest.main()
