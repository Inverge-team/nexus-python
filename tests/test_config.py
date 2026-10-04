import os
import unittest

from nexus import NexusConfig
from nexus.exceptions import ConfigurationError


class ConfigTest(unittest.TestCase):
    def test_requires_api_key(self):
        with self.assertRaises(ConfigurationError):
            NexusConfig(api_key="")

    def test_defaults(self):
        config = NexusConfig(api_key="nxs_abc_def")
        self.assertEqual(config.base_url, "https://services.inverge.net")
        self.assertEqual(config.http_base, "https://services.inverge.net")
        self.assertEqual(config.socket_base, "https://services.inverge.net")
        self.assertTrue(config.resolved_user_agent.startswith("inverge-nexus-python/"))

    def test_strips_trailing_slash_and_validates_scheme(self):
        self.assertEqual(NexusConfig("k", base_url="http://x/").base_url, "http://x")
        with self.assertRaises(ConfigurationError):
            NexusConfig("k", base_url="x.example")

    def test_from_mapping_accepts_both_spellings(self):
        config = NexusConfig.from_mapping({"apiKey": "k", "baseUrl": "http://a", "maxRetries": 5})
        self.assertEqual(config.api_key, "k")
        self.assertEqual(config.base_url, "http://a")
        self.assertEqual(config.max_retries, 5)

        snake = NexusConfig.from_mapping({"api_key": "k", "base_url": "http://a", "max_retries": 5})
        self.assertEqual(snake, config)

    def test_from_env(self):
        env = {"NEXUS_API_KEY": "nxs_env", "NEXUS_BASE_URL": "http://local:4000", "NEXUS_BATCH": "false"}
        config = NexusConfig.from_env(env)
        self.assertEqual(config.api_key, "nxs_env")
        self.assertEqual(config.base_url, "http://local:4000")
        self.assertFalse(config.batch)

    def test_from_env_missing_key(self):
        with self.assertRaises(ConfigurationError):
            NexusConfig.from_env({})

    def test_overrides_beat_environment(self):
        config = NexusConfig.from_env({"NEXUS_API_KEY": "a"}, api_key="b")
        self.assertEqual(config.api_key, "b")

    def test_url_for(self):
        config = NexusConfig("k", base_url="http://a")
        self.assertEqual(config.url_for("/partner/events"), "http://a/partner/events")
        self.assertEqual(config.url_for("partner/events"), "http://a/partner/events")


if __name__ == "__main__":
    unittest.main()
