"""Tests for the DHL carrier module, against recorded response shapes."""

import unittest
from datetime import date, time

from app.carriers.base import NotFound
from app.carriers.dhl import handles, parse_shipment
from app.models import (
    STATUS_DELIVERED,
    STATUS_EXCEPTION,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    STATUS_UNKNOWN,
)


def response(status_code, description="", **extra):
    shipment = {
        "id": "00340434161094042557",
        "service": "parcel-de",
        "status": {
            "statusCode": status_code,
            "status": status_code,
            "description": description,
            "location": {"address": {"addressLocality": "Hamm"}},
        },
    }
    shipment.update(extra)
    return {"shipments": [shipment]}


class TestStatusMapping(unittest.TestCase):
    def test_pre_transit(self):
        self.assertEqual(parse_shipment(response("pre-transit")).status, STATUS_ORDERED)

    def test_transit(self):
        self.assertEqual(parse_shipment(response("transit")).status, STATUS_SHIPPED)

    def test_delivered(self):
        self.assertEqual(parse_shipment(response("delivered")).status, STATUS_DELIVERED)

    def test_failure(self):
        self.assertEqual(parse_shipment(response("failure")).status, STATUS_EXCEPTION)

    def test_unknown_code_is_unknown(self):
        self.assertEqual(parse_shipment(response("something-new")).status, STATUS_UNKNOWN)

    def test_wording_promotes_transit_to_out_for_delivery(self):
        # DHL's status code stays "transit" once the parcel is in the van; only
        # the wording says so, and that is the rung the ladder cares about.
        update = parse_shipment(
            response("transit", "Die Sendung wurde in das Zustellfahrzeug geladen.")
        )
        self.assertEqual(update.status, STATUS_OUT_FOR_DELIVERY)

    def test_wording_in_english(self):
        update = parse_shipment(response("transit", "Loaded onto the delivery vehicle"))
        self.assertEqual(update.status, STATUS_OUT_FOR_DELIVERY)

    def test_unrelated_wording_stays_shipped(self):
        update = parse_shipment(response("transit", "Die Sendung wurde im Zentrum sortiert."))
        self.assertEqual(update.status, STATUS_SHIPPED)


class TestFields(unittest.TestCase):
    def test_delivery_date_and_window(self):
        update = parse_shipment(
            response(
                "transit",
                estimatedTimeOfDelivery="2026-07-31T00:00:00",
                estimatedDeliveryTimeFrame={
                    "estimatedFrom": "2026-07-31T14:00:00",
                    "estimatedThrough": "2026-07-31T18:00:00",
                },
            )
        )
        self.assertEqual(update.expected_date, date(2026, 7, 31))
        self.assertEqual(update.window_start, time(14, 0))
        self.assertEqual(update.window_end, time(18, 0))

    def test_missing_window_is_none_not_a_crash(self):
        update = parse_shipment(response("transit"))
        self.assertIsNone(update.expected_date)
        self.assertIsNone(update.window_start)

    def test_malformed_timestamps_are_ignored(self):
        update = parse_shipment(
            response("transit", estimatedTimeOfDelivery="irgendwann demnächst")
        )
        self.assertIsNone(update.expected_date)

    def test_location_and_carrier(self):
        update = parse_shipment(response("transit"))
        self.assertEqual(update.location, "Hamm")
        self.assertEqual(update.carrier, "DHL")
        self.assertEqual(update.source, "dhl")

    def test_utc_timestamps(self):
        update = parse_shipment(response("transit", estimatedTimeOfDelivery="2026-07-31T00:00:00Z"))
        self.assertEqual(update.expected_date, date(2026, 7, 31))


class TestEmptyResponse(unittest.TestCase):
    def test_no_shipments_raises_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({"shipments": []})

    def test_missing_key_raises_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({})


class TestHandles(unittest.TestCase):
    def test_names_this_module_answers_for(self):
        self.assertTrue(handles("DHL"))
        self.assertTrue(handles("dhl"))
        self.assertTrue(handles("Deutsche Post"))

    def test_other_carriers_are_left_alone(self):
        for other in ("AMZL", "Hermes", "DPD", "", None):
            self.assertFalse(handles(other), other)


if __name__ == "__main__":
    unittest.main()
