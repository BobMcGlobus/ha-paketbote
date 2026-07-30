"""Tests for the state that has to survive a restart."""

import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

from app.models import STATE_WINDOW, STATUS_OUT_FOR_DELIVERY, Shipment
from app.state import Store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "state.db")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _shipment(self, **kwargs):
        base = dict(
            shipment_id="TC030ZF89",
            order_id="302-6054268-4901944",
            tracking_url="https://example.invalid/track",
            title="Solarkabel 4mm²",
            carrier="AMZL",
            status=STATUS_OUT_FOR_DELIVERY,
            stops_remaining=6,
            window_start=time(14, 0),
            window_end=time(18, 0),
            expected_date=date(2026, 7, 31),
            state=STATE_WINDOW,
            last_seen=datetime(2026, 7, 30, 21, 30),
        )
        base.update(kwargs)
        return Shipment(**base)


class TestShipmentRoundTrip(StoreTestCase):
    def test_saved_shipment_comes_back_identical(self):
        original = self._shipment()
        self.store.save(original)
        (restored,) = self.store.all_shipments()
        for attribute in (
            "shipment_id", "order_id", "title", "carrier", "status",
            "stops_remaining", "window_start", "window_end", "expected_date", "state",
        ):
            self.assertEqual(
                getattr(restored, attribute), getattr(original, attribute), attribute
            )

    def test_saving_twice_updates_rather_than_duplicates(self):
        self.store.save(self._shipment())
        self.store.save(self._shipment(stops_remaining=1))
        shipments = self.store.all_shipments()
        self.assertEqual(len(shipments), 1)
        self.assertEqual(shipments[0].stops_remaining, 1)

    def test_optional_fields_survive_being_empty(self):
        self.store.save(
            self._shipment(
                stops_remaining=None, window_start=None, window_end=None, expected_date=None,
                carrier=None,
            )
        )
        (restored,) = self.store.all_shipments()
        self.assertIsNone(restored.stops_remaining)
        self.assertIsNone(restored.window_start)
        self.assertIsNone(restored.expected_date)

    def test_forget_removes_it(self):
        self.store.save(self._shipment())
        self.store.forget("TC030ZF89")
        self.assertEqual(self.store.all_shipments(), [])

    def test_state_survives_a_reopen(self):
        path = Path(self._tmp.name) / "reopen.db"
        first = Store(path)
        first.save(self._shipment())
        first.close()

        second = Store(path)
        self.assertEqual(len(second.all_shipments()), 1)
        second.close()


class TestFieldHealth(StoreTestCase):
    def test_counts_accumulate_per_field(self):
        self.store.record_fields({"status": True, "stops": False})
        self.store.record_fields({"status": True, "stops": False})
        self.store.record_fields({"status": False, "stops": True})

        health = self.store.field_health()
        self.assertEqual(health["status"]["ok"], 2)
        self.assertEqual(health["status"]["fail"], 1)
        self.assertAlmostEqual(health["status"]["rate"], 0.667, places=2)
        self.assertAlmostEqual(health["stops"]["rate"], 0.333, places=2)

    def test_timestamps_track_the_last_outcome(self):
        self.store.record_fields({"status": True}, datetime(2026, 7, 30, 10, 0))
        self.store.record_fields({"status": False}, datetime(2026, 7, 30, 11, 0))
        health = self.store.field_health()["status"]
        self.assertTrue(health["last_ok"].startswith("2026-07-30T10:00"))
        self.assertTrue(health["last_fail"].startswith("2026-07-30T11:00"))


class TestRequestBudget(StoreTestCase):
    def test_counts_per_day(self):
        today = date(2026, 7, 30)
        self.store.count_requests(1, today)
        self.store.count_requests(2, today)
        self.assertEqual(self.store.requests_today(today), 3)

    def test_a_new_day_starts_from_zero(self):
        self.store.count_requests(5, date(2026, 7, 30))
        self.assertEqual(self.store.requests_today(date(2026, 7, 31)), 0)


if __name__ == "__main__":
    unittest.main()
