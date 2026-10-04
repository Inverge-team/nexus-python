import threading
import time
import unittest

from nexus import NexusClient
from support import FakeTransport


def batched_client(**options):
    transport = FakeTransport()
    options.setdefault("base_url", "https://api.test")
    options.setdefault("flush_interval", 0.05)
    client = NexusClient("nxs_test_secret", transport=transport, batch=True, **options)
    return client, transport


class BatchingTest(unittest.TestCase):
    def test_track_does_not_send_immediately(self):
        client, transport = batched_client()
        try:
            client.events.track("a")
            self.assertEqual(transport.requests, [])
            self.assertEqual(client.pending, 1)
        finally:
            client.close()

    def test_flush_sends_one_request_per_context(self):
        client, transport = batched_client()
        try:
            client.events.track("a", distinct_id="u1")
            client.events.track("b", distinct_id="u1")
            client.events.track("c", distinct_id="u2")
            client.flush()
            self.assertEqual(len(transport.requests), 2)
            by_user = {r["json"]["distinctId"]: r["json"]["events"] for r in transport.requests}
            self.assertEqual([e["name"] for e in by_user["u1"]], ["a", "b"])
            self.assertEqual([e["name"] for e in by_user["u2"]], ["c"])
        finally:
            client.close()

    def test_max_batch_triggers_a_send(self):
        client, transport = batched_client(max_batch=3)
        try:
            for i in range(3):
                client.events.track("e{0}".format(i))
            deadline = time.monotonic() + 2
            while not transport.requests and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(transport.requests), 1)
            self.assertEqual(len(transport.last["json"]["events"]), 3)
        finally:
            client.close()

    def test_interval_triggers_a_send(self):
        client, transport = batched_client(flush_interval=0.1)
        try:
            client.logs.info("hello")
            deadline = time.monotonic() + 3
            while not transport.requests and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(transport.requests), 1)
            self.assertEqual(transport.last["path"], "partner/logs")
        finally:
            client.close()

    def test_queue_is_bounded(self):
        # A blocked sender is the realistic way to overflow: the backend is
        # down, the worker is stuck on one request, and items keep arriving.
        from nexus.batcher import BatchQueue

        release = threading.Event()
        sent = []

        def blocking_sender(items, context):
            sent.append(items)
            release.wait(timeout=5)

        queue = BatchQueue("test", blocking_sender, max_batch=2, interval=0.01, max_queue=4)
        try:
            for i in range(50):
                queue.add({"name": "e{0}".format(i)}, {})
            self.assertLessEqual(queue.pending, 4)
            self.assertGreater(queue.dropped, 0)
        finally:
            release.set()
            queue.close(timeout=2)

    def test_close_flushes(self):
        client, transport = batched_client(flush_interval=60)
        client.events.track("a")
        client.close()
        self.assertEqual(len(transport.requests), 1)

    def test_delivery_failure_does_not_raise(self):
        client, transport = batched_client(flush_interval=60)
        try:
            transport.queue_error(500)
            client.events.track("a")
            client.flush()  # must not raise
        finally:
            client.close()


class BackgroundDispatcherTest(unittest.TestCase):
    def test_fire_and_forget(self):
        client, transport = batched_client()
        background = client.background()
        try:
            self.assertIsNone(background.push.send_to_user("u1", title="hi") or None)
            background.flush(timeout=2.0)
            deadline = time.monotonic() + 2
            while not transport.requests and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(transport.last["path"], "partner/push/send")
        finally:
            background.close()
            client.close()


if __name__ == "__main__":
    unittest.main()
