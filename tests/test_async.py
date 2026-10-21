import asyncio
import unittest

from nexus import context
from nexus.aio import AsyncNexusClient
from nexus.exceptions import NotFoundError
from support import FakeAsyncTransport


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_async_client(**options):
    transport = FakeAsyncTransport()
    options.setdefault("base_url", "https://api.test")
    options.setdefault("batch", False)
    options.setdefault("max_retries", 0)
    return AsyncNexusClient("nxs_test_secret", transport=transport, **options), transport


class AsyncClientTest(unittest.TestCase):
    def test_identify_and_emit(self):
        async def scenario():
            client, transport = make_async_client()
            await client.identify("u1", email="a@b.com")
            self.assertEqual(transport.last["path"], "partner/sessions/identify")
            self.assertEqual(context.current().distinct_id, "u1")

            await client.realtime.emit("orders:1", "status", {"state": "shipped"})
            self.assertEqual(transport.last["path"], "partner/rooms/orders%3A1/emit")
            await client.aclose()

        run(scenario())
        context.clear()

    def test_errors_raise(self):
        async def scenario():
            client, transport = make_async_client()
            transport.queue({"error": {"code": "not_found", "message": "nope"}}, 404)
            with self.assertRaises(NotFoundError):
                await client.realtime.rooms()
            await client.aclose()

        run(scenario())

    def test_flags(self):
        async def scenario():
            client, transport = make_async_client()
            transport.queue({"flags": {"beta": "v2"}, "payloads": {"beta": {"x": 1}}})
            self.assertEqual(await client.flags.variant("beta", "u1"), "v2")
            await client.aclose()

        run(scenario())

    def test_push_builder_is_awaitable(self):
        async def scenario():
            client, transport = make_async_client()
            await client.push.notification().title("hi").to_user("u1").send()
            self.assertEqual(transport.last["path"], "partner/push/send")
            self.assertEqual(transport.last["json"]["distinctIds"], ["u1"])
            await client.aclose()

        run(scenario())

    def test_live_activity_builder_is_awaitable(self):
        async def scenario():
            client, transport = make_async_client()
            await client.live_activities.activity("T", "a1").status("Go").start()
            self.assertEqual(transport.last["path"], "partner/live-activities/start")
            await client.live_activities.template("order_tracking", "a2").step("pending").start()
            self.assertEqual(transport.last["json"]["template"], "order_tracking")
            await client.live_activities.activity("a2").step("arrived").update()
            self.assertEqual(transport.last["json"], {"data": {"step": "arrived"}})
            await client.aclose()

        run(scenario())

    def test_batched_events_flush(self):
        async def scenario():
            client, transport = make_async_client(batch=True, flush_interval=0.05)
            client.events.track("a", {"n": 1})
            self.assertEqual(client.pending, 1)
            await client.flush()
            self.assertEqual(transport.last["path"], "partner/events")
            self.assertEqual(transport.last["json"]["events"][0]["name"], "a")
            await client.aclose()

        run(scenario())

    def test_context_manager(self):
        async def scenario():
            transport = FakeAsyncTransport()
            async with AsyncNexusClient(
                "nxs_k", base_url="https://api.test", transport=transport, batch=False
            ) as client:
                await client.sessions.identify("u1")
            self.assertTrue(client.closed)

        run(scenario())

    def test_shares_payload_builders_with_the_sync_client(self):
        # Same endpoint, same body — the two clients can't drift.
        from support import make_client

        sync_client, sync_transport = make_client()
        sync_client.surveys.complete("s1", {"q1": 9}, distinct_id="u1")

        async def scenario():
            client, transport = make_async_client()
            await client.surveys.complete("s1", {"q1": 9}, distinct_id="u1")
            self.assertEqual(transport.last["path"], sync_transport.last["path"])
            self.assertEqual(transport.last["json"], sync_transport.last["json"])
            await client.aclose()

        run(scenario())


if __name__ == "__main__":
    unittest.main()
