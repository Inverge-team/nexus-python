import asyncio
import hashlib
import hmac
import json
import time
import unittest

from support import FakeAsyncTransport, make_client

from nexus.docstore import (
    ConflictError,
    InvalidWebhookSignature,
    ServiceDisabledError,
    array_union,
    count,
    delete_field,
    increment,
    or_,
    server_timestamp,
    sum_,
    to_update_spec,
    verify_webhook,
    where,
)
from nexus.exceptions import PermissionDeniedError

DOC = {"id": "o1", "data": {"total": 5}, "version": 1, "createdAt": "2026-10-28T00:00:00.000Z", "updatedAt": "2026-10-28T00:00:00.000Z"}
SK = "nxsk_abc123abc123_secret"


class FieldValuesTest(unittest.TestCase):
    def test_compile_to_operators(self):
        self.assertEqual(
            to_update_spec({"views": increment(2), "tags": array_union("a"), "at": server_timestamp(), "draft": delete_field(), "a.b": 1}),
            {"$inc": {"views": 2}, "$addToSet": {"tags": {"$each": ["a"]}}, "$serverTime": ["at"], "$unset": ["draft"], "$set": {"a.b": 1}},
        )
        self.assertEqual(to_update_spec({"$inc": {"n": 1}}), {"$inc": {"n": 1}})


class DocStoreSyncTest(unittest.TestCase):
    def test_query_body_and_service_key_header(self):
        client, t = make_client()
        t.queue({"items": [DOC], "nextCursor": "c1", "hasMore": True, "total": 9})
        db = client.docstore(service_key=SK)
        page = (
            db.collection("orders")
            .where("status", "in", ["open", "paid"])
            .where(or_(where("total", ">=", 100), where("vip", "==", True)))
            .order_by("total", desc=True)
            .limit(20)
            .with_count()
            .get()
        )
        self.assertEqual(t.last["method"], "POST")
        self.assertTrue(t.last["url"].endswith("/partner/docstore/collections/orders/query"))
        self.assertEqual(t.last["headers"]["x-nexus-service-key"], SK)
        self.assertEqual(
            t.last["json"],
            {
                "filter": {"$and": [{"status": {"$in": ["open", "paid"]}}, {"$or": [{"total": {"$gte": 100}}, {"vip": {"$eq": True}}]}]},
                "sort": [{"field": "total", "dir": "desc"}],
                "limit": 20,
                "count": True,
            },
        )
        self.assertEqual(page.docs[0]["total"], 5)
        self.assertEqual((page.next_cursor, page.total), ("c1", 9))

    def test_as_user_sends_the_bearer_token(self):
        client, t = make_client()
        t.queue(DOC)
        client.docstore(service_key=SK).as_user("user-jwt").doc("orders/o1").get(expand=["user"])
        self.assertEqual(t.last["headers"]["Authorization"], "Bearer user-jwt")
        self.assertNotIn("x-nexus-service-key", t.last["headers"])
        self.assertIn("expand=user", t.last["url"])

    def test_missing_is_exists_false_and_errors_raise_even_when_silent(self):
        client, t = make_client(silent=True)
        t.queue_error(404, "not_found", "gone")
        t.queue_error(403, "permission_denied", "denied by rules")
        db = client.docstore(service_key=SK)
        self.assertFalse(db.doc("a/b").get().exists)
        with self.assertRaises(PermissionDeniedError):
            db.doc("a/c").get()

    def test_update_uses_field_values_and_precondition(self):
        client, t = make_client()
        t.queue({"index": 0, "status": "ok", "id": "o1", "version": 2, "doc": {**DOC, "version": 2}})
        snap = client.docstore(service_key=SK).doc("orders/o1").update({"total": increment(1)}, if_version=1)
        self.assertEqual(t.last["method"], "PATCH")
        self.assertEqual(t.last["json"], {"update": {"$inc": {"total": 1}}, "precondition": {"version": 1}})
        self.assertEqual(snap.version, 2)

    def test_transaction_retries_on_a_precondition_race(self):
        client, t = make_client()
        state = {"balance": 100, "version": 3, "commits": 0}

        def send(method, url, headers, body, timeout):  # noqa: ARG001
            from nexus.transport import RawResponse

            if method == "GET":
                return RawResponse(200, json.dumps({**DOC, "id": "a", "data": {"balance": state["balance"]}, "version": state["version"]}).encode())
            state["commits"] += 1
            if state["commits"] == 1:
                state["version"] += 1
                return RawResponse(409, json.dumps({"error": {"code": "conflict", "message": "precondition", "details": {"reason": "precondition_failed"}}}).encode())
            op = json.loads(body)["operations"][0]
            assert op["precondition"]["version"] == state["version"]
            state["balance"] = op["data"]["balance"]
            return RawResponse(200, json.dumps({"results": [{"index": 0, "status": "ok", "collection": "accounts", "id": "a", "version": 9}]}).encode())

        t.send = send  # type: ignore[assignment]
        db = client.docstore(service_key=SK)
        ref = db.doc("accounts/a")

        def move(tx):
            snap = tx.get(ref)
            tx.set(ref, {"balance": snap["balance"] - 30})
            return "done"

        self.assertEqual(db.run_transaction(move), "done")
        self.assertEqual(state["commits"], 2)
        self.assertEqual(state["balance"], 70)

    def test_conflict_error_exposes_precondition(self):
        client, t = make_client()
        t.queue({"error": {"code": "conflict", "message": "x", "details": {"reason": "precondition_failed"}}}, 409)
        with self.assertRaises(ConflictError) as ctx:
            client.docstore(service_key=SK).doc("a/b").set({"x": 1}, if_version=1)
        self.assertTrue(ctx.exception.is_precondition_failed)

    def test_switched_off_raises_without_a_request(self):
        client, t = make_client(services={"docstore": False})
        with self.assertRaises(ServiceDisabledError):
            client.docstore(service_key=SK).doc("a/b").get()
        self.assertEqual(t.requests, [])

    def test_change_stream_pages_resume_from_next(self):
        client, t = make_client()
        t.queue({"changes": [{"seq": 4, "collection": "c", "id": "x", "op": "create", "version": 1, "data": {}, "at": "2026-10-28T00:00:00Z"}], "next": 4, "hasMore": True})
        t.queue({"changes": [{"seq": 5, "collection": "c", "id": "y", "op": "delete", "version": 2, "data": None}], "next": 5, "hasMore": False})
        stream = client.docstore(service_key=SK).watch_changes(since=3, poll_interval=0)
        got = [next(stream).seq, next(stream).seq]
        self.assertEqual(got, [4, 5])
        self.assertIn("since=3", t.requests[0]["url"])
        self.assertIn("since=4", t.requests[1]["url"])

    def test_aggregate(self):
        client, t = make_client()
        t.queue({"groups": [{"key": {"status": "paid"}, "values": {"n": 2, "revenue": 30.5}}]})
        groups = client.docstore(service_key=SK).collection("orders").aggregate({"n": count(), "revenue": sum_("total")}, group_by=["status"])
        self.assertEqual(t.last["json"]["aggregations"], {"n": {"op": "count"}, "revenue": {"op": "sum", "field": "total"}})
        self.assertEqual(groups[0]["values"]["revenue"], 30.5)

    def test_usage_cap_is_not_retried(self):
        from nexus.exceptions import RateLimitError

        client, t = make_client(max_retries=3)
        t.queue({"error": {"code": "usage_limit_exceeded", "message": "cap", "details": {"metric": "docstore.reads"}}}, 429)
        with self.assertRaises(RateLimitError) as ctx:
            client.docstore(service_key=SK).doc("a/b").get()
        self.assertEqual(ctx.exception.code, "usage_limit_exceeded")
        self.assertEqual(len(t.requests), 1)

    def test_bad_service_key_is_a_configuration_error(self):
        client, _ = make_client()
        from nexus.exceptions import ConfigurationError

        with self.assertRaises(ConfigurationError):
            client.docstore(service_key="nxs_not_a_service_key")


class WebhookTest(unittest.TestCase):
    def sign(self, body, secret="whsec_x", ts=None, nonce="n1"):
        ts = int(time.time()) if ts is None else ts
        sig = hmac.new(secret.encode(), "{0}.{1}.{2}".format(ts, nonce, body).encode(), hashlib.sha256).hexdigest()
        return {"X-Nexus-Signature": sig, "X-Nexus-Timestamp": str(ts), "X-Nexus-Nonce": nonce}

    def test_valid_stale_tampered_replayed(self):
        body = json.dumps({"event": "docstore.document.create", "id": "o1"})
        self.assertEqual(verify_webhook(body, self.sign(body), "whsec_x")["id"], "o1")
        with self.assertRaises(InvalidWebhookSignature):
            verify_webhook(body, self.sign(body, ts=int(time.time()) - 1000), "whsec_x")
        with self.assertRaises(InvalidWebhookSignature):
            verify_webhook(body + " ", self.sign(body), "whsec_x")
        seen = set()
        verify_webhook(body.encode(), self.sign(body), "whsec_x", seen_nonce=lambda n: n in seen or seen.add(n))
        with self.assertRaises(InvalidWebhookSignature):
            verify_webhook(body, self.sign(body), "whsec_x", seen_nonce=lambda n: n in seen or seen.add(n))


class DocStoreAsyncTest(unittest.TestCase):
    def test_async_query_and_batch(self):
        from nexus.aio import AsyncNexusClient

        t = FakeAsyncTransport()
        t.queue({"items": [DOC], "nextCursor": None, "hasMore": False})
        t.queue({"results": [{"index": 0, "status": "ok", "collection": "orders", "id": "o2", "version": 1}]})

        async def run():
            client = AsyncNexusClient("nxs_test_secret", base_url="https://api.test", transport=t, batch=False, max_retries=0)
            db = client.docstore(service_key=SK)
            page = await db.collection("orders").where("total", ">", 1).get()
            async with db.batch() as b:
                b.create(db.doc("orders/o2"), {"total": 1})
            return page

        page = asyncio.run(run())
        self.assertEqual(page.docs[0].id, "o1")
        self.assertEqual(t.requests[1]["json"]["operations"][0]["type"], "create")


if __name__ == "__main__":
    unittest.main()
