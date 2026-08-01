"""Tests for the parts of the scraper that work without a browser."""

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Config
from app.models import shorten
from app.scraper import identify, looks_active, normalise_text, sanitise_id

ORDER = "028-1234567-1234567"


class TestIdentify(unittest.TestCase):
    def test_the_package_index_decides_even_when_a_shipment_id_is_offered(self):
        # Amazon only adds shipmentId once the parcel is dispatched. Keying on
        # it made a parcel change identity mid-life and be filed twice.
        url = (
            f"https://www.amazon.de/progress-tracker/package/ref=ppx_yo_dt_b_track"
            f"?_encoding=UTF8&orderId={ORDER}&packageIndex=0&shipmentId=DhX7Kq2"
        )
        self.assertEqual(identify(url), (ORDER, f"{ORDER}-0"))

    def test_the_identity_survives_dispatch(self):
        base = f"https://www.amazon.de/gp/your-account/ship-track?orderId={ORDER}&packageIndex=1"
        before = identify(base)
        after = identify(base + "&shipmentId=DhX7Kq2")
        self.assertEqual(before, after)

    def test_a_shipment_id_is_used_when_there_is_no_package_index(self):
        url = f"https://www.amazon.de/progress-tracker/package?orderId={ORDER}&shipmentId=DhX7Kq2"
        self.assertEqual(identify(url), (ORDER, "DhX7Kq2"))

    def test_falls_back_to_order_and_package_index(self):
        url = f"https://www.amazon.de/gp/your-account/ship-track?orderId={ORDER}&packageIndex=2"
        self.assertEqual(identify(url), (ORDER, f"{ORDER}-2"))

    def test_missing_package_index_defaults_to_zero(self):
        url = f"https://www.amazon.de/gp/your-account/ship-track?orderId={ORDER}"
        self.assertEqual(identify(url), (ORDER, f"{ORDER}-0"))

    def test_packages_of_one_order_stay_distinct(self):
        base = "https://www.amazon.de/gp/your-account/ship-track?orderId=" + ORDER
        first = identify(base + "&packageIndex=0")
        second = identify(base + "&packageIndex=1")
        self.assertIsNotNone(first)
        self.assertNotEqual(first[1], second[1])

    def test_order_id_recovered_from_path_when_not_a_parameter(self):
        url = f"https://www.amazon.de/progress-tracker/package/{ORDER}/ref=xyz"
        identity = identify(url)
        self.assertIsNotNone(identity)
        self.assertEqual(identity[0], ORDER)

    def test_link_without_any_order_id_is_rejected(self):
        self.assertIsNone(identify("https://www.amazon.de/gp/css/order-history?ref=nav"))

    def test_case_of_query_keys_does_not_matter(self):
        url = f"https://www.amazon.de/ship-track?OrderID={ORDER}&ShipmentId=ABC123"
        self.assertEqual(identify(url), (ORDER, "ABC123"))


class TestSanitiseId(unittest.TestCase):
    def test_keeps_dashes_and_alphanumerics(self):
        self.assertEqual(sanitise_id(ORDER), ORDER)

    def test_replaces_topic_breaking_characters(self):
        self.assertEqual(sanitise_id("a/b+c#d"), "a_b_c_d")

    def test_strips_leading_and_trailing_separators(self):
        self.assertEqual(sanitise_id("//abc//"), "abc")


class TestLooksActive(unittest.TestCase):
    def test_delivered_card_is_skipped(self):
        self.assertFalse(looks_active("Zugestellt am 28. Juli\nPaket ansehen"))

    def test_arriving_card_is_active(self):
        self.assertTrue(looks_active("Ankunft Freitag, 1. August"))

    def test_today_card_is_active(self):
        self.assertTrue(looks_active("Kommt heute\nZwischen 14 und 18 Uhr"))

    def test_active_wins_over_delivered_in_mixed_card(self):
        # A multi-item order can show one item delivered and another in flight;
        # missing that delivery is worse than one extra request.
        self.assertTrue(looks_active("Zugestellt am 28. Juli\nAnkunft morgen"))

    def test_unrecognised_wording_fails_open(self):
        self.assertTrue(looks_active("Irgendein neuer Amazon-Text"))

    def test_empty_card_fails_open(self):
        self.assertTrue(looks_active(""))

    def test_case_is_ignored(self):
        self.assertFalse(looks_active("ZUGESTELLT"))


class TestNormaliseText(unittest.TestCase):
    def test_squeezes_blank_line_runs_and_trailing_space(self):
        self.assertEqual(normalise_text("a   \n\n\n\n b \n"), "a\n\n b")


class TestShorten(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(shorten("  Ein   langer\nName "), "Ein langer Name")

    def test_truncates_to_the_limit(self):
        result = shorten("x" * 200)
        self.assertEqual(len(result), 60)
        self.assertTrue(result.endswith("…"))


class TestConfig(unittest.TestCase):
    def test_defaults_when_options_file_is_absent(self):
        config = Config.load(Path("/nonexistent/options.json"))
        self.assertEqual(config.amazon_domain, "amazon.de")

    def test_overview_reads_the_plain_order_list(self):
        # Not ?orderFilter=open: that is Amazon's "Not Yet Dispatched" tab and
        # hides exactly the packages that are already on their way.
        self.assertEqual(
            Config().order_history_url, "https://www.amazon.de/gp/css/order-history"
        )

    def test_overview_url_is_not_the_undispatched_filter(self):
        self.assertNotIn("orderFilter=open", Config().order_history_url)
        self.assertIn("orderFilter=open", Config().undispatched_url)

    def test_urls_follow_the_configured_domain(self):
        config = Config(amazon_domain="amazon.co.uk")
        self.assertTrue(config.order_history_url.startswith("https://www.amazon.co.uk/"))
        self.assertTrue(config.undispatched_url.startswith("https://www.amazon.co.uk/"))

    def test_unknown_options_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "options.json"
            path.write_text(json.dumps({"amazon_domain": "amazon.co.uk", "future_option": 1}))
            config = Config.load(path)
            self.assertEqual(config.amazon_domain, "amazon.co.uk")
            self.assertEqual(config.base_url, "https://www.amazon.co.uk")

    def test_malformed_options_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "options.json"
            path.write_text("{not json")
            self.assertEqual(Config.load(path).amazon_domain, "amazon.de")


if __name__ == "__main__":
    unittest.main()
