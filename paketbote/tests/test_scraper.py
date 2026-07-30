"""Tests for the parts of the scraper that work without a browser."""

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Config
from app.models import shorten
from app.scraper import identify, normalise_text, sanitise_id

ORDER = "028-1234567-1234567"


class TestIdentify(unittest.TestCase):
    def test_shipment_id_wins_when_present(self):
        url = (
            f"https://www.amazon.de/progress-tracker/package/ref=ppx_yo_dt_b_track"
            f"?_encoding=UTF8&orderId={ORDER}&packageIndex=0&shipmentId=DhX7Kq2"
        )
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
        self.assertEqual(config.order_history_url, "https://www.amazon.de/gp/css/order-history")

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
