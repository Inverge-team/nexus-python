import asyncio
import io
import unittest

from nexus import context
from nexus.integrations.asgi import NexusASGIMiddleware
from nexus.integrations.wsgi import NexusWSGIMiddleware
from support import make_client


class WsgiTest(unittest.TestCase):
    def _environ(self, path="/orders/42", method="GET"):
        return {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "a=1",
            "SERVER_NAME": "test",
            "REMOTE_ADDR": "1.2.3.4",
            "wsgi.url_scheme": "http",
            "HTTP_HOST": "test",
            "HTTP_X_NEXUS_DISTINCT_ID": "u_1",
            "HTTP_USER_AGENT": "pytest",
            "wsgi.input": io.BytesIO(),
        }

    def _run(self, app, environ):
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status

        body = app(environ, start_response)
        chunks = list(body)
        close = getattr(body, "close", None)
        if close:
            close()
        return captured, chunks

    def test_tracks_a_request_event(self):
        client, transport = make_client()

        def inner(environ, start_response):
            # Context is bound for the duration of the request.
            self.assertEqual(context.current().distinct_id, "u_1")
            start_response("200 OK", [])
            return [b"ok"]

        app = NexusWSGIMiddleware(inner, client)
        captured, chunks = self._run(app, self._environ())
        self.assertEqual(chunks, [b"ok"])
        event = transport.last["json"]["events"][0]
        self.assertEqual(event["name"], "http_request")
        self.assertEqual(event["properties"]["method"], "GET")
        self.assertEqual(event["properties"]["path"], "/orders/42")
        self.assertEqual(event["properties"]["status"], 200)
        self.assertIn("duration_ms", event["properties"])
        self.assertEqual(transport.last["json"]["distinctId"], "u_1")
        self.assertIsNone(context.current().distinct_id)

    def test_ignores_health_checks(self):
        client, transport = make_client()

        def inner(environ, start_response):
            start_response("200 OK", [])
            return [b""]

        app = NexusWSGIMiddleware(inner, client)
        self._run(app, self._environ(path="/healthz"))
        self.assertEqual(transport.requests, [])

    def test_reports_unhandled_exceptions(self):
        client, transport = make_client()

        def inner(environ, start_response):
            raise RuntimeError("boom")

        app = NexusWSGIMiddleware(inner, client)
        with self.assertRaises(RuntimeError):
            self._run(app, self._environ())
        paths = transport.paths()
        self.assertIn("/partner/errors", paths)
        error = [r for r in transport.requests if r["path"] == "partner/errors"][0]
        self.assertEqual(error["json"]["type"], "RuntimeError")
        self.assertFalse(error["json"]["handled"])

    def test_query_string_is_not_recorded_by_default(self):
        client, transport = make_client()

        def inner(environ, start_response):
            start_response("200 OK", [])
            return [b""]

        app = NexusWSGIMiddleware(inner, client)
        self._run(app, self._environ())
        self.assertNotIn("query", transport.last["json"]["events"][0]["properties"])


class AsgiTest(unittest.TestCase):
    def _scope(self, path="/orders/42"):
        return {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"a=1",
            "scheme": "http",
            "headers": [(b"host", b"test"), (b"x-nexus-distinct-id", b"u_9")],
            "client": ("5.6.7.8", 1234),
        }

    def _run(self, app, scope):
        messages = []

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            messages.append(message)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(app(scope, receive, send))
        finally:
            loop.close()
        return messages

    def test_tracks_a_request_event(self):
        client, transport = make_client()

        async def inner(scope, receive, send):
            self.assertEqual(context.current().distinct_id, "u_9")
            await send({"type": "http.response.start", "status": 201, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        app = NexusASGIMiddleware(inner, client)
        self._run(app, self._scope())
        event = transport.last["json"]["events"][0]
        self.assertEqual(event["properties"]["status"], 201)
        self.assertEqual(event["properties"]["method"], "POST")
        self.assertEqual(transport.last["json"]["distinctId"], "u_9")

    def test_passes_through_non_http_scopes(self):
        client, transport = make_client()
        seen = []

        async def inner(scope, receive, send):
            seen.append(scope["type"])

        app = NexusASGIMiddleware(inner, client)
        self._run(app, {"type": "lifespan"})
        self.assertEqual(seen, ["lifespan"])
        self.assertEqual(transport.requests, [])

    def test_reports_unhandled_exceptions(self):
        client, transport = make_client()

        async def inner(scope, receive, send):
            raise ValueError("bad")

        app = NexusASGIMiddleware(inner, client)
        with self.assertRaises(ValueError):
            self._run(app, self._scope())
        self.assertIn("/partner/errors", transport.paths())


if __name__ == "__main__":
    unittest.main()
