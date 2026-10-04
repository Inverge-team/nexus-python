import threading
import unittest

from nexus import context


class ContextTest(unittest.TestCase):
    def setUp(self):
        context.clear()

    def test_scope_binds_and_restores(self):
        self.assertIsNone(context.current().distinct_id)
        with context.scope(distinct_id="u1"):
            self.assertEqual(context.current().distinct_id, "u1")
            with context.scope(url="/a"):
                self.assertEqual(context.current().distinct_id, "u1")
                self.assertEqual(context.current().url, "/a")
            self.assertIsNone(context.current().url)
        self.assertIsNone(context.current().distinct_id)

    def test_properties_merge(self):
        with context.scope(properties={"a": 1}):
            with context.scope(properties={"b": 2}):
                self.assertEqual(dict(context.current().properties), {"a": 1, "b": 2})

    def test_wire_uses_camel_case(self):
        with context.scope(distinct_id="u1", session_key="s1", app_version="1.0"):
            self.assertEqual(
                context.current().wire(),
                {"distinctId": "u1", "sessionKey": "s1", "appVersion": "1.0"},
            )

    def test_reset_starts_a_new_session(self):
        context.identify("u1", device_key="d1")
        context.reset()
        self.assertIsNone(context.current().distinct_id)
        self.assertEqual(context.current().device_key, "d1")
        self.assertTrue(context.current().session_key.startswith("sess_"))

    def test_isolated_per_thread(self):
        seen = {}

        def worker():
            seen["before"] = context.current().distinct_id
            with context.scope(distinct_id="thread_user"):
                seen["inside"] = context.current().distinct_id

        with context.scope(distinct_id="main_user"):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
            self.assertEqual(context.current().distinct_id, "main_user")

        self.assertIsNone(seen["before"])
        self.assertEqual(seen["inside"], "thread_user")


if __name__ == "__main__":
    unittest.main()
