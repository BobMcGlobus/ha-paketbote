"""Tests for reading Hermes off their own tracking endpoint."""

import unittest
from datetime import time
from unittest.mock import Mock, patch

from app.carriers.base import NotFound
from app.carriers.hermes import HermesTracker, handles, parse_shipment
from app.models import (
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
)


def response(delivered=False, history=None, forecast=None, city="Hamburg"):
    return [{
        "barcode": "12345678901234",
        "parcelType": "PARCEL",
        "shipmentDetails": {
            "parcelAttributes": {"delivered": delivered, "national": True},
            "history": history or [],
            "forecast": forecast or {},
            "address": {"city": city, "zipCode": "33604"},
        },
    }]


class TestStatusMapping(unittest.TestCase):
    def test_a_known_parcel_with_no_history_is_only_announced(self):
        self.assertEqual(parse_shipment(response()).status, STATUS_ORDERED)

    def test_history_means_it_is_moving(self):
        update = parse_shipment(
            response(history=[{"historyText": "Sendung im Logistikzentrum sortiert"}])
        )
        self.assertEqual(update.status, STATUS_SHIPPED)

    def test_out_for_delivery_is_read_from_the_wording(self):
        update = parse_shipment(
            response(history=[{"historyText": "Die Sendung ist im Zustellfahrzeug"}])
        )
        self.assertEqual(update.status, STATUS_OUT_FOR_DELIVERY)

    def test_the_delivered_flag_wins(self):
        update = parse_shipment(response(delivered=True))
        self.assertEqual(update.status, STATUS_DELIVERED)

    def test_delivered_wording_without_the_flag(self):
        update = parse_shipment(
            response(history=[{"historyText": "Sendung wurde zugestellt"}])
        )
        self.assertEqual(update.status, STATUS_DELIVERED)

    def test_the_newest_entry_decides(self):
        update = parse_shipment(response(history=[
            {"historyText": "Sendung angekündigt"},
            {"historyText": "Die Sendung ist in Zustellung"},
        ]))
        self.assertEqual(update.status, STATUS_OUT_FOR_DELIVERY)


class TestForecast(unittest.TestCase):
    """Hermes names a window in UTC; the household thinks in local time."""

    def test_the_window_is_converted_to_local_time(self):
        update = parse_shipment(response(forecast={
            "deliveryTimeFromUTC": "2026-07-31T08:00:00Z",
            "deliveryTimeToUTC": "2026-07-31T12:00:00Z",
        }))
        # Whatever the machine's zone, the two ends stay four hours apart.
        self.assertIsNotNone(update.window_start)
        self.assertIsNotNone(update.window_end)
        span = (
            update.window_end.hour * 60 + update.window_end.minute
            - update.window_start.hour * 60 - update.window_start.minute
        )
        self.assertEqual(span % (24 * 60), 4 * 60)

    def test_a_naive_timestamp_is_taken_as_utc(self):
        update = parse_shipment(response(forecast={
            "deliveryTimeFromUTC": "2026-07-31T08:00:00",
            "deliveryTimeToUTC": "2026-07-31T12:00:00",
        }))
        self.assertIsNotNone(update.window_start)

    def test_no_forecast_is_none_not_a_crash(self):
        update = parse_shipment(response())
        self.assertIsNone(update.window_start)
        self.assertIsNone(update.window_end)

    def test_a_malformed_timestamp_is_ignored(self):
        update = parse_shipment(response(forecast={"deliveryTimeFromUTC": "bald"}))
        self.assertIsNone(update.window_start)


class TestFields(unittest.TestCase):
    def test_location_and_carrier(self):
        update = parse_shipment(response(city="Bielefeld"))
        self.assertEqual(update.location, "Bielefeld")
        self.assertEqual(update.carrier, "Hermes")
        self.assertEqual(update.source, "hermes")

    def test_a_wrapped_list_is_also_accepted(self):
        update = parse_shipment({"shipments": response()})
        self.assertEqual(update.carrier, "Hermes")


class TestEmptyResponse(unittest.TestCase):
    def test_an_empty_list_is_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment([])

    def test_nonsense_is_not_found_rather_than_a_crash(self):
        with self.assertRaises(NotFound):
            parse_shipment("nope")


class TestTracker(unittest.TestCase):
    def test_it_needs_no_credentials(self):
        self.assertTrue(HermesTracker().available)

    def test_an_unknown_number_is_not_found_not_an_error(self):
        for status in (400, 404):
            with patch("app.carriers.scraping.requests.get",
                       return_value=Mock(status_code=status, ok=False)):
                with self.assertRaises(NotFound):
                    HermesTracker().fetch("12345678901234")

    def test_the_probe_accepts_a_refused_number_as_proof_of_life(self):
        for status in (200, 400, 404):
            with patch("app.carriers.scraping.requests.get",
                       return_value=Mock(status_code=status, ok=status == 200)):
                ok, detail = HermesTracker().probe()
            self.assertTrue(ok, f"HTTP {status}: {detail}")

    def test_a_server_fault_is_reported(self):
        with patch("app.carriers.scraping.requests.get",
                   return_value=Mock(status_code=503, ok=False)):
            ok, detail = HermesTracker().probe()
        self.assertFalse(ok)
        self.assertIn("503", detail)


class TestBudget(unittest.TestCase):
    def test_web_reads_are_counted_in_their_own_bucket(self):
        store = Mock()
        with patch("app.carriers.scraping.requests.get",
                   return_value=Mock(status_code=200, ok=True,
                                     json=Mock(return_value=response()))):
            HermesTracker(store).fetch("12345678901234")
        store.count_carrier_request.assert_called_once_with("hermes_web")


class TestHandles(unittest.TestCase):
    def test_names_this_module_answers_for(self):
        self.assertTrue(handles("Hermes"))
        self.assertTrue(handles("hermes"))

    def test_other_carriers_are_left_alone(self):
        for other in ("DHL", "UPS", "DPD", "", None):
            self.assertFalse(handles(other), other)


if __name__ == "__main__":
    unittest.main()
