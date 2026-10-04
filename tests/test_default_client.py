import unittest

import nexus
from nexus.exceptions import ConfigurationError
from support import FakeTransport


class DefaultClientTest(unittest.TestCase):
    def setUp(self):
        nexus.shutdown()
        nexus.context.clear()
        self.transport = FakeTransport()

    def tearDown(self):
        nexus.shutdown()
        nexus.context.clear()

    def _init(self, **options):
        from nexus import NexusClient

        client = NexusClient(
            "nxs_test_secret",
            base_url="https://api.test",
            batch=False,
            transport=self.transport,
            **options
        )
        nexus.set_client(client)
        return client

    def test_raises_before_init(self):
        with self.assertRaises(ConfigurationError):
            nexus.get_client()
        self.assertIsNone(nexus.get_client(required=False))
        self.assertFalse(nexus.is_initialized())

    def test_module_level_helpers(self):
        self._init()
        nexus.track("report_generated", {"rows": 12})
        self.assertEqual(self.transport.last["path"], "partner/events")
        nexus.log("warn", "slow query")
        self.assertEqual(self.transport.last["json"]["logs"][0]["level"], "warn")
        nexus.emit("room", "ping", {"n": 1})
        self.assertEqual(self.transport.last["path"], "partner/rooms/room/emit")

    def test_resource_attribute_access(self):
        self._init()
        nexus.events.track("x")
        self.assertEqual(self.transport.last["path"], "partner/events")
        nexus.push.send_to_user("u1", title="hi")
        self.assertEqual(self.transport.last["path"], "partner/push/send")

    def test_unknown_attribute_still_raises(self):
        self._init()
        with self.assertRaises(AttributeError):
            nexus.not_a_resource

    def test_identify_binds_context(self):
        self._init()
        nexus.identify("u1")
        self.assertEqual(nexus.context.current().distinct_id, "u1")
        nexus.track("after_login")
        self.assertEqual(self.transport.last["json"]["distinctId"], "u1")

    def test_init_from_environment(self):
        env = {"NEXUS_API_KEY": "nxs_env_key", "NEXUS_BASE_URL": "https://api.test"}
        import os

        saved = dict(os.environ)
        os.environ.update(env)
        try:
            client = nexus.init(batch=False, capture_errors=False)
            self.assertEqual(client.config.api_key, "nxs_env_key")
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_flush_is_safe_when_uninitialised(self):
        nexus.flush()  # must not raise


if __name__ == "__main__":
    unittest.main()
