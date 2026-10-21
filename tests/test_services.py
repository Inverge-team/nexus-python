"""Per-service switches: NEXUS_<SERVICE>_ENABLED / services={...}.

Pinned:
  - everything on by default; one variable switches one service off
  - NEXUS_ENABLED=false switches all off
  - off wins: code can't re-enable what the environment switched off
  - a switched-off service makes no request, never raises (even when the
    client is NOT silent), and returns its neutral value
  - events/logs aren't even buffered
  - unknown service names are rejected; camelCase names accepted
"""

import asyncio
import os
import unittest
from unittest import mock

from support import make_client

from nexus import ConfigurationError
from nexus._services import SERVICE_ENV, service_for_path
from nexus.config import NexusConfig


class SwitchConfigTest(unittest.TestCase):
    def test_all_on_by_default(self):
        c = NexusConfig.create("nxs_k", environ={})
        for service in SERVICE_ENV:
            self.assertTrue(c.service_enabled(service), service)

    def test_env_switches_one_service(self):
        c = NexusConfig.create("nxs_k", environ={"NEXUS_LIVE_ACTIVITIES_ENABLED": "false", "NEXUS_PUSH_ENABLED": "off"})
        self.assertFalse(c.service_enabled("live_activities"))
        self.assertFalse(c.service_enabled("push"))
        self.assertTrue(c.service_enabled("events"))

    def test_master_switch(self):
        c = NexusConfig.create("nxs_k", environ={"NEXUS_ENABLED": "0", "NEXUS_PUSH_ENABLED": "true"})
        self.assertFalse(c.service_enabled("push"))
        self.assertFalse(c.enabled)

    def test_off_wins_over_code(self):
        c = NexusConfig.create("nxs_k", environ={"NEXUS_PUSH_ENABLED": "false"}, services={"push": True})
        self.assertFalse(c.service_enabled("push"))
        self.assertEqual(c.services_off["push"], "NEXUS_PUSH_ENABLED=false")

    def test_code_switches_and_camel_case(self):
        c = NexusConfig.create("nxs_k", environ={}, services={"liveActivities": False, "remoteConfig": False})
        self.assertFalse(c.service_enabled("live_activities"))
        self.assertFalse(c.service_enabled("remote_config"))

    def test_unknown_service_rejected(self):
        with self.assertRaises(ConfigurationError):
            NexusConfig.create("nxs_k", environ={}, services={"puhs": False})

    def test_with_recomputes(self):
        c = NexusConfig.create("nxs_k", environ={})
        with mock.patch.dict(os.environ, {}, clear=False):
            d = c.with_(services={"voice": False})
        self.assertFalse(d.service_enabled("voice"))
        self.assertTrue(d.service_enabled("push"))

    def test_path_mapping(self):
        self.assertEqual(service_for_path("/partner/live-activities/o1/update"), "live_activities")
        self.assertEqual(service_for_path("/partner/rooms/orders%3A1/emit"), "realtime")
        self.assertEqual(service_for_path("/partner/voice/legs"), "voice")
        self.assertIsNone(service_for_path("/partner/nothing"))


class SwitchedOffClientTest(unittest.TestCase):
    def test_no_request_no_raise_neutral_results(self):
        client, transport = make_client(services={"live_activities": False, "push": False, "flags": False, "remote_config": False, "events": False})
        self.assertFalse(client.config.silent)  # gating must not rely on silent mode

        self.assertEqual(client.live_activities.template("order_tracking", "o1").step("pending").to_user("u").start(), {})
        client.live_activities.activity("o1").step("arrived").update()
        client.push.notification().title("Hi").to_user("u").send()
        self.assertFalse(client.flags.is_enabled("new_checkout", distinct_id="u"))
        self.assertEqual(client.remote_config.get("banner", "fallback"), "fallback")
        client.events.track("x")
        client.flush()
        self.assertEqual(transport.requests, [])
        self.assertFalse(client.enabled("push"))

    def test_other_services_still_work(self):
        client, transport = make_client(services={"push": False}, batch=False)
        client.events.track("x")
        self.assertEqual(transport.last["path"], "partner/events")


class SwitchedOffAsyncTest(unittest.TestCase):
    def test_async_client_gated(self):
        from support import FakeAsyncTransport

        from nexus.aio import AsyncNexusClient

        async def scenario():
            transport = FakeAsyncTransport()
            client = AsyncNexusClient("nxs_test_secret", transport=transport, base_url="https://api.test", batch=False, max_retries=0, services={"live_activities": False})
            await client.live_activities.template("t", "o").step("a").start()
            self.assertEqual(transport.requests, [])
            await client.aclose()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
