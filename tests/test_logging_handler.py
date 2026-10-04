import logging
import unittest

from nexus import NexusLogHandler
from support import make_client


class LogHandlerTest(unittest.TestCase):
    def setUp(self):
        self.client, self.transport = make_client()
        self.logger = logging.getLogger("tests.handler")
        self.logger.handlers = []
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)
        self.handler = NexusLogHandler(self.client, level=logging.DEBUG)
        self.logger.addHandler(self.handler)

    def tearDown(self):
        self.logger.removeHandler(self.handler)
        self.client.close()

    def test_levels_are_mapped(self):
        self.logger.warning("careful")
        line = self.transport.last["json"]["logs"][0]
        self.assertEqual(line["level"], "warn")
        self.assertEqual(line["message"], "careful")
        self.assertEqual(line["source"], "tests.handler")

    def test_extra_becomes_context(self):
        self.logger.info("paid", extra={"order_id": 7})
        context = self.transport.last["json"]["logs"][0]["context"]
        self.assertEqual(context["order_id"], 7)
        self.assertEqual(context["logger"], "tests.handler")

    def test_exception_is_also_reported_as_an_error(self):
        try:
            raise ValueError("kaput")
        except ValueError:
            self.logger.exception("charge failed")
        paths = self.transport.paths()
        self.assertIn("/partner/logs", paths)
        self.assertIn("/partner/errors", paths)
        error = [r for r in self.transport.requests if r["path"] == "partner/errors"][0]
        self.assertEqual(error["json"]["type"], "ValueError")

    def test_sdk_own_logs_are_not_forwarded(self):
        logging.getLogger("nexus").addHandler(self.handler)
        try:
            logging.getLogger("nexus").error("internal")
            self.assertEqual(self.transport.requests, [])
        finally:
            logging.getLogger("nexus").removeHandler(self.handler)

    def test_handler_without_a_client_is_inert(self):
        handler = NexusLogHandler()
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", (), None)
        handler.emit(record)  # must not raise


if __name__ == "__main__":
    unittest.main()


class FeedbackLoopTest(unittest.TestCase):
    """The SDK's own HTTP client logs must not become Nexus logs."""

    def test_records_logged_during_delivery_are_dropped(self):
        client, transport = make_client()
        chatty = logging.getLogger("httpx")
        chatty.handlers = []
        chatty.propagate = False
        chatty.setLevel(logging.INFO)
        handler = NexusLogHandler(client, level=logging.DEBUG)
        chatty.addHandler(handler)

        # Simulate what httpx does from inside a Nexus request.
        from nexus._logging import delivering

        try:
            with delivering():
                chatty.info('HTTP Request: POST /partner/logs "HTTP/1.1 200 OK"')
            self.assertEqual(transport.requests, [])

            # Outside a delivery the same record is forwarded normally.
            chatty.info("HTTP Request: GET https://example.com")
            self.assertEqual(len(transport.requests), 1)
        finally:
            chatty.removeHandler(handler)
            client.close()

    def test_real_delivery_does_not_recurse(self):
        # Attach to the root logger — the arrangement that used to loop.
        client, transport = make_client()
        root = logging.getLogger()
        handler = NexusLogHandler(client, level=logging.DEBUG)
        saved_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            logging.getLogger("app").info("hello")
            self.assertEqual(len(transport.requests), 1)
        finally:
            root.removeHandler(handler)
            root.setLevel(saved_level)
            client.close()
