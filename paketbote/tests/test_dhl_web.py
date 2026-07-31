"""Tests for reading DHL off dhl.de, against the recorded response shape."""

import unittest
from datetime import date, time
from unittest.mock import Mock, patch

from app.carriers.base import NotFound, RateLimited
from app.carriers.dhl_web import DhlWebTracker, parse_shipment
from app.models import (
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    STATUS_UNKNOWN,
)


def response(step, last_step=5, delivered=False, events=None, **details):
    body = {
        "sendungsdetails": {
            "sendungsverlauf": {
                "fortschritt": step,
                "maximalFortschritt": last_step,
                "events": events or [],
            },
            "istZugestellt": delivered,
        },
    }
    body["sendungsdetails"].update(details)
    return {"sendungen": [body], "rateLimited": False}


class TestProgressMapping(unittest.TestCase):
    """The stage is a position, not a word — the same trick as Amazon's bar."""

    def test_nothing_yet(self):
        self.assertEqual(parse_shipment(response(0)).status, STATUS_UNKNOWN)

    def test_announced(self):
        self.assertEqual(parse_shipment(response(1)).status, STATUS_ORDERED)

    def test_on_its_way(self):
        for step in (2, 3):
            self.assertEqual(parse_shipment(response(step)).status, STATUS_SHIPPED, step)

    def test_out_for_delivery(self):
        self.assertEqual(parse_shipment(response(4)).status, STATUS_OUT_FOR_DELIVERY)

    def test_the_last_stage_is_delivery(self):
        self.assertEqual(parse_shipment(response(5)).status, STATUS_DELIVERED)

    def test_the_flag_beats_the_position(self):
        self.assertEqual(parse_shipment(response(2, delivered=True)).status, STATUS_DELIVERED)

    def test_a_shorter_ladder_still_ends_in_delivery(self):
        # The last rung is read from the maximum, not assumed to be five.
        self.assertEqual(parse_shipment(response(3, last_step=3)).status, STATUS_DELIVERED)

    def test_wording_promotes_transit_to_out_for_delivery(self):
        update = parse_shipment(
            response(2, events=[{"status": "Die Sendung wurde in das Zustellfahrzeug geladen."}])
        )
        self.assertEqual(update.status, STATUS_OUT_FOR_DELIVERY)

    def test_unrelated_wording_stays_shipped(self):
        update = parse_shipment(
            response(2, events=[{"status": "Die Sendung wurde im Zentrum sortiert."}])
        )
        self.assertEqual(update.status, STATUS_SHIPPED)

    def test_a_missing_progress_block_is_not_a_crash(self):
        update = parse_shipment({"sendungen": [{"sendungsdetails": {}}]})
        self.assertEqual(update.status, STATUS_UNKNOWN)


class TestFields(unittest.TestCase):
    def test_the_newest_event_supplies_wording_and_place(self):
        update = parse_shipment(
            response(2, events=[
                {"status": "Im Paketzentrum", "ort": "Hamm"},
                {"status": "Im Zustellfahrzeug", "ort": "Bielefeld"},
            ])
        )
        self.assertEqual(update.location, "Bielefeld")
        self.assertEqual(update.description, "Im Zustellfahrzeug")

    def test_a_delivery_date_is_found_wherever_it_sits(self):
        update = parse_shipment(response(2, zustellDatum="2026-07-31"))
        self.assertEqual(update.expected_date, date(2026, 7, 31))

    def test_a_german_date_is_understood(self):
        update = parse_shipment(response(2, zustellung={"zustellDatum": "31.07.2026"}))
        self.assertEqual(update.expected_date, date(2026, 7, 31))

    def test_a_window_in_free_text_is_read(self):
        update = parse_shipment(
            response(4, zustellung={"zustellzeitfenster": "zwischen 14 und 18 Uhr"})
        )
        self.assertEqual(update.window_start, time(14, 0))
        self.assertEqual(update.window_end, time(18, 0))

    def test_no_window_is_none_not_a_crash(self):
        update = parse_shipment(response(2))
        self.assertIsNone(update.window_start)
        self.assertIsNone(update.expected_date)

    def test_the_source_says_which_way_it_was_read(self):
        update = parse_shipment(response(2))
        self.assertEqual(update.carrier, "DHL")
        self.assertEqual(update.source, "dhl_web")


class TestNotFound(unittest.TestCase):
    def test_dhl_says_nothing_known_with_a_200(self):
        payload = response(0)
        payload["sendungen"][0]["sendungNichtGefunden"] = {"keineDatenVerfuegbar": True}
        with self.assertRaises(NotFound):
            parse_shipment(payload)

    def test_an_empty_list_is_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({"sendungen": []})

    def test_rate_limiting_is_reported_as_such(self):
        payload = response(2)
        payload["rateLimited"] = True
        with self.assertRaises(RateLimited):
            parse_shipment(payload)


class TestTracker(unittest.TestCase):
    def test_it_needs_no_credentials(self):
        self.assertTrue(DhlWebTracker().available)

    def test_the_postcode_is_passed_on_when_known(self):
        get = Mock(return_value=Mock(status_code=200, ok=True,
                                     json=Mock(return_value=response(2))))
        with patch("app.carriers.scraping.requests.get", get):
            DhlWebTracker().fetch("00340434161094042557", "33604")
        self.assertEqual(get.call_args.kwargs["params"]["zip"], "33604")

    def test_no_postcode_means_no_parameter(self):
        get = Mock(return_value=Mock(status_code=200, ok=True,
                                     json=Mock(return_value=response(2))))
        with patch("app.carriers.scraping.requests.get", get):
            DhlWebTracker().fetch("00340434161094042557")
        self.assertNotIn("zip", get.call_args.kwargs["params"])

    def test_every_call_is_counted(self):
        store = Mock()
        get = Mock(return_value=Mock(status_code=200, ok=True,
                                     json=Mock(return_value=response(2))))
        with patch("app.carriers.scraping.requests.get", get):
            DhlWebTracker(store).fetch("00340434161094042557")
        # Counted apart from the API key's own 250-a-day allowance.
        store.count_carrier_request.assert_called_once_with("dhl_web")


if __name__ == "__main__":
    unittest.main()
