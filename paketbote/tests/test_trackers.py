"""Picking the right carrier module, and building them from the settings."""

import unittest

from app.carriers import trackers
from app.carriers.registry import lookup
from app.config import Config


class TestKeyFor(unittest.TestCase):
    def test_each_carrier_finds_its_own_module(self):
        self.assertEqual(trackers.key_for("DHL"), "dhl")
        self.assertEqual(trackers.key_for("UPS"), "ups")
        self.assertEqual(trackers.key_for("FedEx"), "fedex")
        self.assertEqual(trackers.key_for("DPD"), "dpd")
        self.assertEqual(trackers.key_for("Hermes"), "hermes")

    def test_a_carrier_we_cannot_ask_gets_no_module(self):
        for other in ("GLS", "AMZL", "", None):
            self.assertEqual(trackers.key_for(other), "", other)

    def test_no_two_modules_claim_the_same_name(self):
        seen = set()
        for module in trackers.MODULES.values():
            overlap = seen & module.HANDLES
            self.assertFalse(overlap, f"claimed twice: {overlap}")
            seen |= module.HANDLES


class TestBuild(unittest.TestCase):
    def test_every_module_gets_a_tracker(self):
        built = trackers.build(Config())
        self.assertEqual(set(built), set(trackers.MODULES))

    def test_carriers_with_only_an_api_need_credentials(self):
        built = trackers.build(Config())
        for key in ("ups", "fedex"):
            self.assertFalse(built[key].available, key)

    def test_carriers_readable_from_the_web_work_without_credentials(self):
        built = trackers.build(Config())
        for key in ("dhl", "hermes", "dpd"):
            self.assertTrue(built[key].available, key)
            self.assertEqual(built[key].tier, "web", key)

    def test_a_key_is_preferred_over_the_website(self):
        self.assertEqual(trackers.build(Config(dhl_api_key="k"))["dhl"].tier, "api")

    def test_turning_the_web_fallback_off_leaves_only_the_apis(self):
        built = trackers.build(Config(web_fallback=False))
        self.assertFalse(built["dhl"].available)
        self.assertFalse(built["hermes"].available)
        self.assertFalse(built["dpd"].available)

    def test_credentials_reach_the_trackers(self):
        config = Config(dhl_api_key="k", ups_client_id="i", ups_client_secret="s",
                        fedex_client_id="i", fedex_client_secret="s")
        for key, tracker in trackers.build(config).items():
            self.assertTrue(tracker.available, key)

    def test_turning_the_web_fallback_off_shows_up_in_the_fingerprint(self):
        self.assertNotEqual(trackers.credentials(Config()),
                            trackers.credentials(Config(web_fallback=False)))

    def test_a_changed_secret_shows_up_in_the_fingerprint(self):
        before = trackers.credentials(Config())
        after = trackers.credentials(Config(ups_client_secret="new"))
        self.assertNotEqual(before, after)


class TestPollMinutes(unittest.TestCase):
    def test_each_carrier_has_its_own_interval(self):
        config = Config(dhl_poll_minutes=30, ups_poll_minutes=45, fedex_poll_minutes=60,
                        hermes_poll_minutes=90)
        self.assertEqual(trackers.poll_minutes(config, "dhl"), 30)
        self.assertEqual(trackers.poll_minutes(config, "ups"), 45)
        self.assertEqual(trackers.poll_minutes(config, "fedex"), 60)
        self.assertEqual(trackers.poll_minutes(config, "hermes"), 90)
        # A carrier with no interval of its own borrows the DHL setting.
        self.assertEqual(trackers.poll_minutes(Config(dhl_poll_minutes=25), "dpd"), 30)


class TestRegistryAgrees(unittest.TestCase):
    def test_the_carriers_we_can_ask_are_marked_automatic(self):
        for key in trackers.MODULES:
            info = lookup(key)
            self.assertIsNotNone(info, key)
            self.assertTrue(info.automatic, key)


if __name__ == "__main__":
    unittest.main()
