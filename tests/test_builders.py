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


if __name__ == "__main__":
    unittest.main()
