import unittest

from support import make_client


class PushBuilderTest(unittest.TestCase):
    def test_full_payload(self):
        client, transport = make_client()
        (
            client.push.notification()
            .title("Your order shipped")
            .body("Track it in the app")
            .image("https://cdn/box.png")
            .data(screen="/orders/42")
            .button("track", "Track", url="https://x/track")
            .ios_badge(1)
            .android_visibility("public")
            .to_segments(["vip", "active_7d"])
            .send()
        )
        body = transport.last["json"]
        self.assertEqual(body["title"], "Your order shipped")
        self.assertEqual(body["imageUrl"], "https://cdn/box.png")
        self.assertEqual(body["data"], {"screen": "/orders/42"})
        self.assertEqual(body["segmentIds"], ["vip", "active_7d"])
        self.assertEqual(body["options"]["iosBadge"], 1)
        self.assertEqual(body["options"]["androidVisibility"], "public")
        self.assertEqual(body["options"]["buttons"], [{"id": "track", "text": "Track", "url": "https://x/track"}])

    def test_requires_title_and_target(self):
        client, _ = make_client()
        with self.assertRaises(ValueError):
            client.push.notification().to_user("u").send()
        with self.assertRaises(ValueError):
            client.push.notification().title("t").send()

    def test_targets_are_deduplicated(self):
        client, _ = make_client()
        payload = client.push.notification().title("t").to_user("u").to_users(["u", "v"]).to_dict()
        self.assertEqual(payload["distinctIds"], ["u", "v"])

    def test_to_all(self):
        client, transport = make_client()
        client.push.notification().title("t").to_all().send()
        self.assertTrue(transport.last["json"]["all"])

    def test_where_filter(self):
        client, _ = make_client()
        payload = client.push.notification().title("t").where(platform="ios").to_dict()
        self.assertEqual(payload["filter"], {"platform": "ios"})


class LiveActivityBuilderTest(unittest.TestCase):
    def test_start(self):
        client, transport = make_client()
        (
            client.live_activities.activity("DeliveryAttributes", "order_42")
            .title("Order #42")
            .status("Preparing")
            .progress(20)
            .to_user("user_1")
            .priority(10)
            .start()
        )
        body = transport.last["json"]
        self.assertEqual(body["activityType"], "DeliveryAttributes")
        self.assertEqual(body["activityId"], "order_42")
        self.assertEqual(body["contentState"], {"title": "Order #42", "status": "Preparing", "progress": 20})
        self.assertEqual(body["distinctIds"], ["user_1"])
        self.assertEqual(body["priority"], 10)

    def test_update_and_end_without_a_type(self):
        client, transport = make_client()
        client.live_activities.activity("order_42").status("On the way").progress(70).update()
        self.assertEqual(transport.last["path"], "partner/live-activities/order_42/update")

        client.live_activities.activity("order_42").status("Delivered").dismiss_at("2026-01-01T12:00:00Z").end()
        body = transport.last["json"]
        self.assertEqual(body["contentState"], {"status": "Delivered"})
        self.assertEqual(body["dismissalDate"], "2026-01-01T12:00:00Z")

    def test_start_requires_a_type(self):
        client, _ = make_client()
        with self.assertRaises(ValueError):
            client.live_activities.activity("order_42").status("x").start()


class LiveActivityTemplateTest(unittest.TestCase):
    def test_templated_start(self):
        import datetime as dt

        client, transport = make_client()
        (
            client.live_activities.template("order_tracking", "order_42")
            .step("pending")
            .eta_at(dt.datetime(2026, 10, 21, 12, 0, 0))  # naive = UTC
            .value("driver", "Ahmed")
            .values(liters=500)
            .to_user("user_1")
            .lang("ckb")
            .alert("Order received", "500 L")
            .start()
        )
        body = transport.last["json"]
        self.assertEqual(transport.last["path"], "partner/live-activities/start")
        self.assertEqual(body["template"], "order_tracking")
        self.assertNotIn("activityType", body)  # the server targets the SDK widget's type
        self.assertNotIn("contentState", body)
        self.assertEqual(body["data"], {"step": "pending", "etaAt": 1792584000, "values": {"driver": "Ahmed", "liters": 500}})
        self.assertEqual(body["distinctIds"], ["user_1"])
        self.assertEqual(body["lang"], "ckb")
        self.assertEqual(body["alert"], {"title": "Order received", "body": "500 L"})

    def test_partial_update_and_final_end(self):
        client, transport = make_client()
        client.live_activities.activity("order_42").step("arrived").progress(0.5).update()
        self.assertEqual(transport.last["path"], "partner/live-activities/order_42/update")
        self.assertEqual(transport.last["json"], {"data": {"step": "arrived", "progress": 0.5}})

        client.live_activities.activity("order_42").step("completed").end()
        self.assertEqual(transport.last["json"], {"data": {"step": "completed"}})

        client.live_activities.activity("order_42").canceled().end()
        self.assertEqual(transport.last["json"], {"data": {"state": "canceled"}})

    def test_progress_alone_stays_plain(self):
        client, transport = make_client()
        client.live_activities.activity("order_42").status("On the way").progress(70).update()
        self.assertEqual(transport.last["json"], {"contentState": {"status": "On the way", "progress": 70}})

    def test_plain_methods_and_inline_template(self):
        client, transport = make_client()
        client.live_activities.start_template({"layout": "steps", "steps": [{"id": "a"}, {"id": "b"}]}, "o1", {"step": "a"})
        self.assertEqual(len(transport.last["json"]["template"]["steps"]), 2)
        client.live_activities.update_data("o1", {"step": "b", "etaAt": None})
        self.assertEqual(transport.last["json"], {"data": {"step": "b", "etaAt": None}})  # None clears it
        client.live_activities.end("o1", data={"state": "done"})
        self.assertEqual(transport.last["json"], {"data": {"state": "done"}})

    def test_eta_accepts_ms(self):
        client, transport = make_client()
        client.live_activities.template("t", "o").eta_at(1792584000123).start()
        self.assertEqual(transport.last["json"]["data"]["etaAt"], 1792584000)

    def test_start_needs_a_template_or_type(self):
        client, _ = make_client()
        with self.assertRaisesRegex(ValueError, "template"):
            client.live_activities.activity("o").step("a").start()


if __name__ == "__main__":
    unittest.main()
