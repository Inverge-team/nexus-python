"""Nexus Telescope: the recorder, and the framework integrations that are installed."""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import nexus.telescope as telescope
from nexus import NexusClient
from nexus.telescope._recorder import Recorder
from support import FakeTransport

try:
    import requests  # type: ignore
except ImportError:  # pragma: no cover
    requests = None


def make(**options):
    transport = FakeTransport()
    client = NexusClient("nxs_test", base_url="https://api.example.test", transport=transport, batch=False)
    telescope._reset_for_tests()
    tel = telescope.Telescope(Recorder(client, **options))
    telescope._instance = tel  # what the middlewares find via telescope.get()
    return tel, transport


def batches(transport):
    tel = telescope.get()
    tel.flush()
    out = []
    for r in transport.requests:
        if r["path"] == "partner/telescope":
            out.extend(r["json"]["batches"])
    return out


class _Echo(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps({"traceparent": self.headers.get("traceparent")}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


class RecorderTests(unittest.TestCase):
    def test_journey_spans_logs_and_trace(self):
        tel, t = make()
        rec = tel.recorder
        batch = rec.begin("request", {"method": "GET", "uri": "/x", "headers": {"Authorization": "Bearer s"}}, traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
        token = rec.activate(batch)
        tel.user(7, email="a@b.c")
        with tel.span("pricing"):
            tel.query("select * from prices where sku = %s", ["A1"], 1.5, "postgresql")
        tel.log("warning", "low stock", sku="A1", password="p")
        rec.end(batch, {"responseStatus": 200}, "200")
        rec.deactivate(token)
        [b] = batches(t)
        self.assertEqual(b["traceId"], "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(b["parentSpanId"], "00f067aa0ba902b7")
        self.assertEqual(b["content"]["headers"]["Authorization"], "********")
        self.assertEqual(b["user"], {"id": "7", "email": "a@b.c"})
        span, query, log = b["entries"]
        self.assertEqual(span["type"], "span")
        self.assertEqual(query["parentId"], span["id"])
        self.assertEqual(log["content"]["context"]["password"], "********")

    def test_job_joins_trace_and_failures_are_kept(self):
        tel, t = make(sample_rate=0.0)
        rec = tel.recorder
        batch = rec.begin("request", {"uri": "/checkout"})
        token = rec.activate(batch)
        carrier = {"order": 1, **tel.inject("send_invoice", "emails")}
        rec.end(batch, {"responseStatus": 200}, "200")
        rec.deactivate(token)
        with self.assertRaises(RuntimeError):
            with tel.job("send_invoice", carrier=carrier):
                raise RuntimeError("smtp down")
        bs = batches(t)
        self.assertEqual([b["type"] for b in bs], ["job"], "the 200 request was sampled out, the failed job kept")
        self.assertEqual(bs[0]["status"], "failed")
        self.assertEqual(bs[0]["parentSpanId"], carrier["__nexusTelescope"].split("-")[2])

    def test_switch_off(self):
        import os

        os.environ["NEXUS_TELESCOPE_ENABLED"] = "false"
        try:
            transport = FakeTransport()
            client = NexusClient("nxs_test", base_url="https://api.example.test", transport=transport, batch=False)
            rec = Recorder(client)
            self.assertIsNone(rec.begin("request"))
        finally:
            del os.environ["NEXUS_TELESCOPE_ENABLED"]

    @unittest.skipIf(requests is None, "requests not installed")
    def test_requests_propagates_traceparent(self):
        tel, t = make()
        from nexus.telescope.instrument import instrument_requests

        instrument_requests(tel.recorder)
        server = HTTPServer(("127.0.0.1", 0), _Echo)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        rec = tel.recorder
        batch = rec.begin("command", {"command": "sync"})
        token = rec.activate(batch)
        got = requests.get("http://127.0.0.1:{0}/ping".format(server.server_port)).json()
        rec.end(batch, {}, "0")
        rec.deactivate(token)
        server.shutdown()
        [b] = batches(t)
        http = [e for e in b["entries"] if e["type"] == "http"][0]
        self.assertEqual(got["traceparent"], "00-{0}-{1}-01".format(b["traceId"], http["id"]))
        self.assertEqual(http["status"], "200")
        self.assertEqual(http["content"]["response"], got)


class FrameworkTests(unittest.TestCase):
    def test_fastapi(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi not installed")
        tel, t = make()
        app = FastAPI()
        app.add_middleware(telescope.TelescopeASGIMiddleware)

        @app.get("/orders/{order_id}")
        async def show(order_id: int):
            tel.user(order_id)
            with tel.span("load"):
                await asyncio.sleep(0.005)
                tel.query("select 1", [], 0.3)
            return {"id": order_id}

        @app.get("/boom")
        async def boom():
            raise ValueError("kaput")

        client = TestClient(app, raise_server_exceptions=False)
        self.assertEqual(client.get("/orders/5").json(), {"id": 5})
        self.assertEqual(client.get("/boom").status_code, 500)
        self.assertEqual(client.get("/health").status_code, 404)
        bs = batches(t)
        ok = [b for b in bs if b["content"]["path"] == "/orders/5"][0]
        self.assertEqual(ok["content"]["route"], "/orders/{order_id}")
        self.assertEqual(ok["content"]["response"], {"id": 5})
        span = [e for e in ok["entries"] if e["type"] == "span"][0]
        query = [e for e in ok["entries"] if e["type"] == "query"][0]
        self.assertEqual(query["parentId"], span["id"])
        failed = [b for b in bs if b["content"]["path"] == "/boom"][0]
        self.assertEqual(failed["status"], "500")
        self.assertTrue(any(e["type"] == "exception" and e["content"]["message"] == "kaput" for e in failed["entries"]))
        self.assertFalse([b for b in bs if b["content"]["path"] == "/health"], "ignored path")

    def test_flask_with_sqlalchemy(self):
        try:
            from flask import Flask, jsonify
            from sqlalchemy import create_engine, text
        except ImportError:
            self.skipTest("flask/sqlalchemy not installed")
        tel, t = make()
        from nexus.telescope.instrument import instrument_sqlalchemy

        instrument_sqlalchemy(tel.recorder)
        engine = create_engine("sqlite://")
        with engine.begin() as c:
            c.execute(text("create table items (id integer, order_id integer)"))
        app = Flask(__name__)
        telescope.instrument_flask(app)

        @app.post("/orders/<int:oid>")
        def place(oid):
            with engine.connect() as c:
                for i in range(6):
                    c.execute(text("select * from items where order_id = :o"), {"o": i})
            return jsonify(ok=True)

        res = app.test_client().post("/orders/3", json={"qty": 2, "card_number": "4111"})
        self.assertEqual(res.get_json(), {"ok": True})  # reading the body ends the journey, as a server would
        [b] = batches(t)
        self.assertEqual(b["content"]["route"], "/orders/<int:oid>")
        self.assertEqual(b["content"]["payload"], {"qty": 2, "card_number": "********"})
        queries = [e for e in b["entries"] if e["type"] == "query"]
        self.assertEqual(len(queries), 6)
        self.assertEqual(queries[0]["content"]["connection"], "sqlite")

    def test_django(self):
        try:
            import django
            from django.conf import settings
        except ImportError:
            self.skipTest("django not installed")
        tel, t = make()
        if not settings.configured:
            settings.configure(
                DEBUG=False,
                SECRET_KEY="x",
                ROOT_URLCONF=__name__,
                ALLOWED_HOSTS=["*"],
                MIDDLEWARE=["nexus.telescope.integrations.DjangoTelescopeMiddleware"],
                DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
                INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
            )
            django.setup()
        if not settings.DATABASES.get("default", {}).get("ENGINE") or settings.ROOT_URLCONF != __name__:
            self.skipTest("Django was already configured by another test in this process; run this module alone")
        from django.db import connection
        from django.test import Client

        with connection.cursor() as c:
            c.execute("create table if not exists t (id integer)")
        res = Client().get("/orders/9")
        self.assertEqual(res.status_code, 200)
        res = Client(raise_request_exception=False).get("/fail")
        self.assertEqual(res.status_code, 500)
        bs = batches(t)
        ok = [b for b in bs if b["content"]["path"] == "/orders/9"][0]
        self.assertEqual(ok["content"]["route"], "/orders/<int:oid>")
        self.assertEqual(sum(1 for e in ok["entries"] if e["type"] == "query"), 5)
        failed = [b for b in bs if b["content"]["path"] == "/fail"][0]
        self.assertEqual(failed["status"], "500")
        self.assertEqual(len([e for e in failed["entries"] if e["type"] == "exception"]), 1, "reported once")


# Django URLconf for test_django
def _django_urls():
    try:
        from django.http import JsonResponse
        from django.urls import path
    except ImportError:  # pragma: no cover
        return []

    def show(request, oid):
        from django.db import connection

        with connection.cursor() as c:
            for i in range(5):
                c.execute("select * from t where id = %s", [i])
        return JsonResponse({"id": oid})

    def fail(request):
        raise LookupError("no such order")

    return [path("orders/<int:oid>", show), path("fail", fail)]


urlpatterns = _django_urls()

if __name__ == "__main__":
    unittest.main()
