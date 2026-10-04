import unittest

from nexus import RoomMessage, context
from support import make_client


class SessionsTest(unittest.TestCase):
    def test_identify(self):
        client, transport = make_client()
        client.sessions.identify("u1", email="a@b.com", traits={"plan": "pro"})
        req = transport.last
        self.assertEqual(req["path"], "partner/sessions/identify")
        self.assertEqual(req["json"], {"distinctId": "u1", "email": "a@b.com", "traits": {"plan": "pro"}})

    def test_track_uses_ambient_context(self):
        client, transport = make_client()
        with context.scope(distinct_id="u1", session_key="s1", url="/checkout"):
            client.sessions.track(country="IQ")
        body = transport.last["json"]
        self.assertEqual(body["distinctId"], "u1")
        self.assertEqual(body["sessionKey"], "s1")
        self.assertEqual(body["country"], "IQ")
        self.assertEqual(body["entryUrl"], "/checkout")


class EventsTest(unittest.TestCase):
    def test_track_sends_immediately_when_unbatched(self):
        client, transport = make_client()
        client.events.track("order_placed", {"total": 42})
        req = transport.last
        self.assertEqual(req["path"], "partner/events")
        self.assertEqual(req["json"]["events"], [{"name": "order_placed", "properties": {"total": 42}}])

    def test_kwargs_become_properties(self):
        client, transport = make_client()
        client.events.track("signup", plan="pro")
        self.assertEqual(transport.last["json"]["events"][0]["properties"], {"plan": "pro"})

    def test_default_and_ambient_properties_merge(self):
        client, transport = make_client(default_properties={"tier": "free"})
        with context.scope(properties={"region": "erbil"}):
            client.events.track("x", {"tier": "pro"})
        self.assertEqual(
            transport.last["json"]["events"][0]["properties"],
            {"tier": "pro", "region": "erbil"},
        )

    def test_config_defaults_land_in_context(self):
        client, transport = make_client(release="abc123", app_version="1.2.3")
        client.events.track("x")
        body = transport.last["json"]
        self.assertEqual(body["release"], "abc123")
        self.assertEqual(body["appVersion"], "1.2.3")

    def test_batch_returns_written(self):
        client, transport = make_client()
        transport.queue({"written": 2})
        self.assertEqual(client.events.batch([{"name": "a"}, {"name": "b"}]), 2)

    def test_empty_batch_short_circuits(self):
        client, transport = make_client()
        self.assertEqual(client.events.batch([]), 0)
        self.assertEqual(transport.requests, [])


class LogsTest(unittest.TestCase):
    def test_levels(self):
        client, transport = make_client()
        for method, level in (
            (client.logs.trace, "trace"),
            (client.logs.debug, "debug"),
            (client.logs.info, "info"),
            (client.logs.warn, "warn"),
            (client.logs.error, "error"),
            (client.logs.fatal, "fatal"),
        ):
            method("m")
            self.assertEqual(transport.last["json"]["logs"][0]["level"], level)

    def test_context_and_source(self):
        client, transport = make_client()
        client.logs.info("payment started", source="checkout", order_id=7)
        line = transport.last["json"]["logs"][0]
        self.assertEqual(line["source"], "checkout")
        self.assertEqual(line["context"], {"order_id": 7})


class ErrorsTest(unittest.TestCase):
    def test_capture_message(self):
        client, transport = make_client()
        client.errors.capture("boom", type="GatewayTimeout", fingerprint="gw")
        body = transport.last["json"]
        self.assertEqual(transport.last["path"], "partner/errors")
        self.assertEqual(body["message"], "boom")
        self.assertEqual(body["type"], "GatewayTimeout")
        self.assertEqual(body["fingerprint"], "gw")

    def test_capture_exception_builds_a_stack(self):
        client, transport = make_client()
        try:
            raise ValueError("bad input")
        except ValueError as exc:
            client.errors.capture_exception(exc, context={"feature": "checkout"})
        body = transport.last["json"]
        self.assertEqual(body["type"], "ValueError")
        self.assertEqual(body["message"], "bad input")
        self.assertTrue(body["handled"])
        self.assertEqual(body["context"], {"feature": "checkout"})
        self.assertGreaterEqual(len(body["stack"]), 1)
        self.assertIn("test_resources.py", body["stack"][0]["filename"])

    def test_capture_exception_reads_sys_exc_info(self):
        client, transport = make_client()
        try:
            raise KeyError("missing")
        except KeyError:
            client.errors.capture_exception()
        self.assertEqual(transport.last["json"]["type"], "KeyError")

    def test_cause_chain_is_included(self):
        client, transport = make_client()
        try:
            try:
                raise ValueError("inner")
            except ValueError as inner:
                raise RuntimeError("outer") from inner
        except RuntimeError as exc:
            client.errors.capture_exception(exc)
        functions = [f["function"] for f in transport.last["json"]["stack"]]
        self.assertTrue(any("caused by ValueError" in str(f) for f in functions))


class FlagsTest(unittest.TestCase):
    def test_evaluate(self):
        client, transport = make_client()
        transport.queue({"flags": {"a": True, "b": "v2"}, "payloads": {"b": {"x": 1}}})
        result = client.flags.evaluate("u1", {"plan": "pro"})
        self.assertTrue(result.is_enabled("a"))
        self.assertTrue(result.is_enabled("b"))
        self.assertEqual(result.variant("b"), "v2")
        self.assertIsNone(result.variant("a"))
        self.assertEqual(result.payload("b"), {"x": 1})
        self.assertEqual(transport.last["json"], {"distinctId": "u1", "properties": {"plan": "pro"}})

    def test_distinct_id_from_context(self):
        client, transport = make_client()
        transport.queue({"flags": {"a": True}})
        with context.scope(distinct_id="ctx_user"):
            self.assertTrue(client.flags.is_enabled("a"))
        self.assertEqual(transport.last["json"]["distinctId"], "ctx_user")

    def test_requires_a_distinct_id(self):
        client, _ = make_client()
        with self.assertRaises(ValueError):
            client.flags.evaluate()


class RemoteConfigTest(unittest.TestCase):
    def _payload(self):
        return {
            "version": 3,
            "etag": "W/abc",
            "parameters": {
                "phone": {"value": "+964", "valueType": "STRING", "source": "Duhok"},
                "max": {"value": 10, "valueType": "NUMBER", "source": None},
                "on": {"value": True, "valueType": "BOOLEAN"},
            },
        }

    def test_fetch_and_typed_getters(self):
        client, transport = make_client()
        transport.queue(self._payload())
        snapshot = client.remote_config.fetch({"user_properties": {"governorate": "Duhok"}})
        self.assertEqual(snapshot.version, 3)
        self.assertEqual(snapshot.get_string("phone"), "+964")
        self.assertEqual(snapshot.get_int("max"), 10)
        self.assertTrue(snapshot.get_bool("on"))
        self.assertEqual(snapshot.source_of("phone"), "Duhok")
        self.assertEqual(transport.last["json"]["userProperties"], {"governorate": "Duhok"})
        self.assertEqual(transport.last["json"]["platform"], "server")

    def test_defaults_fill_missing_keys(self):
        client, transport = make_client()
        transport.queue(self._payload())
        client.remote_config.set_defaults({"missing": "fallback"})
        self.assertEqual(client.remote_config.get("missing"), "fallback")

    def test_cache_avoids_a_second_call(self):
        client, transport = make_client()
        transport.queue(self._payload())
        client.remote_config.get("phone")
        client.remote_config.get("max")
        self.assertEqual(len(transport.requests), 1)
        client.remote_config.invalidate()
        transport.queue(self._payload())
        client.remote_config.get("phone")
        self.assertEqual(len(transport.requests), 2)

    def test_not_modified_keeps_previous_values(self):
        client, transport = make_client()
        transport.queue(self._payload())
        client.remote_config.get("phone")
        client.remote_config.cache_ttl = 0  # force revalidation
        transport.queue({"notModified": True})
        self.assertEqual(client.remote_config.get_string("phone"), "+964")
        self.assertEqual(transport.requests[-1]["headers"].get("If-None-Match"), "W/abc")

    def test_set_publishes(self):
        client, transport = make_client()
        transport.queue({"key": "fee"}).queue({"versionNumber": 4})
        client.remote_config.set("fee", 2.5)
        self.assertEqual(transport.requests[0]["method"], "PUT")
        self.assertEqual(transport.requests[0]["json"]["valueType"], "NUMBER")
        self.assertEqual(transport.requests[1]["path"], "partner/remote-config/publish")


class RealtimeTest(unittest.TestCase):
    def test_emit_single_and_multiple_events(self):
        client, transport = make_client()
        client.realtime.emit("orders:42", "status", {"state": "shipped"})
        self.assertEqual(transport.last["path"], "partner/rooms/orders%3A42/emit")
        self.assertEqual(transport.last["json"], {"events": ["status"], "payload": {"state": "shipped"}})

        client.realtime.emit("r", ["a", "b"], 1)
        self.assertEqual(transport.last["json"]["events"], ["a", "b"])

    def test_emit_with_room_message(self):
        client, transport = make_client()
        client.realtime.emit(RoomMessage("r", "e", {"v": 1}))
        self.assertEqual(transport.last["json"], {"events": ["e"], "payload": {"v": 1}})

    def test_broadcast_and_emit_to_rooms(self):
        client, transport = make_client()
        client.realtime.emit_to_rooms(["a", "b"], "ping", {"n": 1})
        self.assertEqual(transport.last["path"], "partner/rooms/emit")
        self.assertEqual(
            transport.last["json"]["rooms"],
            [
                {"name": "a", "events": ["ping"], "payload": {"n": 1}},
                {"name": "b", "events": ["ping"], "payload": {"n": 1}},
            ],
        )

    def test_broadcast_accepts_loose_dicts(self):
        client, transport = make_client()
        client.realtime.broadcast([{"room": "a", "event": "x", "payload": 1}])
        self.assertEqual(transport.last["json"]["rooms"][0], {"name": "a", "events": ["x"], "payload": 1})

    def test_room_registry(self):
        client, transport = make_client()
        transport.queue({"rooms": [{"id": "1"}]})
        self.assertEqual(client.realtime.rooms(), [{"id": "1"}])
        self.assertEqual(transport.last["method"], "GET")

        client.realtime.delete_room("r 1")
        self.assertEqual(transport.last["method"], "DELETE")
        self.assertEqual(transport.last["path"], "partner/rooms/r%201")

        client.realtime.link("a", "b")
        self.assertEqual(transport.last["path"], "partner/rooms/a/link/b")

    def test_schema(self):
        client, transport = make_client()
        client.realtime.set_schema("r", {"type": "object"})
        self.assertEqual(transport.last["json"], {"schema": {"type": "object"}})
        client.realtime.enable_schema("r", False)
        self.assertEqual(transport.last["json"], {"enabled": False})


class SurveysTest(unittest.TestCase):
    def test_active(self):
        client, transport = make_client()
        transport.queue({"surveys": [{"id": "s1"}]})
        self.assertEqual(client.surveys.active(distinct_id="u1"), [{"id": "s1"}])

    def test_respond_sends_an_object_for_answers(self):
        client, transport = make_client()
        client.surveys.dismiss("s1", distinct_id="u1")
        body = transport.last["json"]
        self.assertEqual(body["answers"], {})
        self.assertTrue(body["dismissed"])

    def test_complete(self):
        client, transport = make_client()
        client.surveys.complete("s1", {"q1": 9})
        self.assertTrue(transport.last["json"]["completed"])


class PushTest(unittest.TestCase):
    def test_send_requires_a_title(self):
        client, _ = make_client()
        with self.assertRaises(ValueError):
            client.push.send(["u1"], {})

    def test_send_to_user(self):
        client, transport = make_client()
        client.push.send_to_user("u1", title="Welcome")
        self.assertEqual(transport.last["path"], "partner/push/send")
        self.assertEqual(transport.last["json"], {"title": "Welcome", "distinctIds": ["u1"]})

    def test_register_and_unregister(self):
        client, transport = make_client()
        client.push.register_token("tok", "web")
        self.assertEqual(transport.last["json"]["provider"], "webpush")
        client.push.register_token("tok", "android")
        self.assertEqual(transport.last["json"]["provider"], "fcm")
        client.push.unregister("tok")
        self.assertEqual(transport.last["json"], {"token": "tok"})


class InAppTest(unittest.TestCase):
    def test_events(self):
        client, transport = make_client()
        client.inapp.click("m1", "cta", distinct_id="u1")
        self.assertEqual(
            transport.last["json"],
            {"messageId": "m1", "type": "click", "buttonId": "cta", "distinctId": "u1"},
        )


class LiveActivitiesTest(unittest.TestCase):
    def test_start_update_end(self):
        client, transport = make_client()
        client.live_activities.start("Delivery", "order_42", {"status": "Preparing"}, distinct_ids=["u1"])
        self.assertEqual(transport.last["path"], "partner/live-activities/start")
        self.assertEqual(transport.last["json"]["contentState"], {"status": "Preparing"})

        client.live_activities.update("order 42", {"status": "On the way"}, priority=5)
        self.assertEqual(transport.last["path"], "partner/live-activities/order%2042/update")

        client.live_activities.end("order_42", dismissal_date="2026-01-01T12:00:00Z")
        self.assertEqual(transport.last["path"], "partner/live-activities/order_42/end")
        self.assertEqual(transport.last["json"]["dismissalDate"], "2026-01-01T12:00:00Z")

    def test_empty_content_state_is_an_object(self):
        client, transport = make_client()
        client.live_activities.start("T", "id")
        self.assertEqual(transport.last["json"]["contentState"], {})


class LinksTest(unittest.TestCase):
    def test_attribute(self):
        client, transport = make_client()
        client.links.install(click_id="abc", name="summer", platform="ios")
        body = transport.last["json"]
        self.assertEqual(body["type"], "install")
        self.assertEqual(body["clickId"], "abc")
        self.assertEqual(body["platform"], "ios")


class ReplayTest(unittest.TestCase):
    def test_ingest(self):
        client, transport = make_client()
        client.replay.ingest("rec1", [{"type": 2, "timestamp": 1}], href="/checkout")
        body = transport.last["json"]
        self.assertEqual(body["recordingId"], "rec1")
        self.assertEqual(body["href"], "/checkout")
        self.assertEqual(len(body["events"]), 1)

    def test_rejects_oversized_chunks(self):
        client, _ = make_client()
        with self.assertRaises(ValueError):
            client.replay.ingest("rec1", [{"type": 2}] * 5001)


class VoiceTest(unittest.TestCase):
    def test_create_call_and_leg(self):
        client, transport = make_client()
        client.voice.create_call(direction="outbound", recording_mode="mixed")
        self.assertEqual(transport.last["path"], "partner/voice/calls")

        client.voice.add_leg("sess1", "caller", "webrtc", identity_id="agent_1")
        self.assertEqual(transport.last["path"], "partner/voice/legs")
        self.assertEqual(transport.last["json"]["role"], "caller")

    def test_validates_enums(self):
        client, _ = make_client()
        with self.assertRaises(ValueError):
            client.voice.add_leg("s", "nope", "webrtc")
        with self.assertRaises(ValueError):
            client.voice.add_leg("s", "caller", "carrier-pigeon")
        with self.assertRaises(ValueError):
            client.voice.presence("i", "d", "SLEEPING")

    def test_leg_actions(self):
        client, transport = make_client()
        client.voice.hangup("leg1", reason="busy")
        self.assertEqual(transport.last["path"], "partner/voice/legs/hangup")
        self.assertEqual(transport.last["json"], {"legId": "leg1", "reason": "busy"})

    def test_recording_url(self):
        client, _ = make_client()
        url = client.voice.recording_download_url("tok", inline=True)
        self.assertEqual(url, "https://api.test/partner/voice/recordings/download?token=tok&inline=1")


if __name__ == "__main__":
    unittest.main()


class FlagDegradationTest(unittest.TestCase):
    """A flag outage must not take the calling page down."""

    def test_helpers_fall_back_to_the_default(self):
        client, transport = make_client()
        transport.queue_error(503, "internal_error", "down")
        self.assertTrue(client.flags.is_enabled("x", "u1", default=True))

        transport.queue_error(503)
        self.assertEqual(client.flags.variant("x", "u1", default="control"), "control")

        transport.queue_error(503)
        self.assertEqual(client.flags.payload("x", "u1", default={"a": 1}), {"a": 1})

    def test_evaluate_still_raises(self):
        from nexus.exceptions import ServerError

        client, transport = make_client()
        transport.queue_error(503)
        with self.assertRaises(ServerError):
            client.flags.evaluate("u1")

    def test_variant_falls_back_when_the_flag_is_off(self):
        client, transport = make_client()
        transport.queue({"flags": {"other": True}, "payloads": {}})
        self.assertEqual(client.flags.variant("x", "u1", default="control"), "control")


class ConfigLayeringTest(unittest.TestCase):
    """Explicit options sit on top of the NEXUS_* environment."""

    def test_env_base_url_applies_to_an_explicit_key(self):
        import os

        from nexus import NexusClient
        from support import FakeTransport

        saved = dict(os.environ)
        os.environ["NEXUS_BASE_URL"] = "https://staging.test"
        os.environ["NEXUS_BATCH"] = "false"
        try:
            client = NexusClient("nxs_explicit", transport=FakeTransport())
            self.assertEqual(client.config.api_key, "nxs_explicit")
            self.assertEqual(client.config.base_url, "https://staging.test")
            self.assertFalse(client.config.batch)
            client.close()
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_explicit_options_beat_the_environment(self):
        import os

        from nexus import NexusConfig

        config = NexusConfig.create(
            "nxs_k", environ={"NEXUS_BASE_URL": "https://env.test"}, base_url="https://explicit.test"
        )
        self.assertEqual(config.base_url, "https://explicit.test")
        self.assertIsNotNone(os)
