"""Falling through from one way of asking a carrier to the next."""

import unittest
from unittest.mock import Mock

from app.carriers.base import CarrierError, CarrierUpdate, NotFound, RateLimited
from app.carriers.chain import Chain
from app.models import STATUS_SHIPPED


def update(source):
    return CarrierUpdate(status=STATUS_SHIPPED, expected_date=None, window_start=None,
                         window_end=None, location="", description="", carrier="X",
                         source=source)


def member(tier, available=True, result=None, error=None, wants_postcode=False):
    m = Mock()
    m.tier = tier
    m.available = available
    m.wants_postcode = wants_postcode
    m.fetch = Mock(side_effect=error) if error else Mock(return_value=result or update(tier))
    return m


class TestOrder(unittest.TestCase):
    def test_the_first_usable_way_is_used(self):
        api, web = member("api"), member("web")
        result = Chain("X", [api, web]).fetch("123")
        self.assertEqual(result.source, "api")
        web.fetch.assert_not_called()

    def test_a_way_without_credentials_is_skipped(self):
        api, web = member("api", available=False), member("web")
        result = Chain("X", [api, web]).fetch("123")
        self.assertEqual(result.source, "web")
        api.fetch.assert_not_called()

    def test_the_tier_names_what_would_be_tried(self):
        self.assertEqual(Chain("X", [member("api", available=False), member("web")]).tier, "web")
        self.assertEqual(Chain("X", [member("api"), member("web")]).tier, "api")

    def test_a_chain_with_nothing_usable_is_unavailable(self):
        chain = Chain("X", [member("api", available=False)])
        self.assertFalse(chain.available)
        self.assertEqual(chain.tier, "")


class TestFallingThrough(unittest.TestCase):
    def test_a_rejected_key_falls_through_to_the_website(self):
        # Exactly the situation a DHL key stuck at 401 produces.
        api = member("api", error=CarrierError("HTTP 401 — Unauthorized"))
        web = member("web")
        result = Chain("DHL", [api, web]).fetch("123")
        self.assertEqual(result.source, "web")

    def test_rate_limiting_also_falls_through(self):
        api = member("api", error=RateLimited("daily limit reached"))
        web = member("web")
        self.assertEqual(Chain("DHL", [api, web]).fetch("123").source, "web")

    def test_not_found_is_final(self):
        # Both ways ask the same carrier; a second request would learn nothing.
        api = member("api", error=NotFound("unknown number"))
        web = member("web")
        with self.assertRaises(NotFound):
            Chain("DHL", [api, web]).fetch("123")
        web.fetch.assert_not_called()

    def test_when_every_way_fails_the_reasons_are_kept(self):
        api = member("api", error=CarrierError("HTTP 401"))
        web = member("web", error=CarrierError("HTTP 503"))
        with self.assertRaises(CarrierError) as caught:
            Chain("DHL", [api, web]).fetch("123")
        self.assertIn("401", str(caught.exception))
        self.assertIn("503", str(caught.exception))


class TestPostcode(unittest.TestCase):
    def test_the_chain_wants_a_postcode_if_any_usable_way_does(self):
        self.assertTrue(Chain("DHL", [member("api", wants_postcode=True)]).wants_postcode)
        self.assertFalse(Chain("UPS", [member("api")]).wants_postcode)

    def test_an_unusable_way_does_not_ask_for_one(self):
        chain = Chain("DHL", [member("api", available=False, wants_postcode=True)])
        self.assertFalse(chain.wants_postcode)


class TestProbe(unittest.TestCase):
    def _member(self, tier, ok, detail, available=True):
        m = Mock()
        m.tier, m.available = tier, available
        m.probe = Mock(return_value=(ok, detail))
        return m

    def test_the_first_working_way_is_reported(self):
        chain = Chain("DHL", [self._member("api", False, "HTTP 401"),
                              self._member("web", True, "dhl.de answers")])
        ok, detail = chain.probe()
        self.assertTrue(ok)
        self.assertIn("web", detail)

    def test_when_none_work_every_reason_is_named(self):
        chain = Chain("DHL", [self._member("api", False, "HTTP 401"),
                              self._member("web", False, "HTTP 503")])
        ok, detail = chain.probe()
        self.assertFalse(ok)
        self.assertIn("401", detail)
        self.assertIn("503", detail)


if __name__ == "__main__":
    unittest.main()
