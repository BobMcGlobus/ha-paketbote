"""Tests for the add-on interface."""

import json
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path

from app import web
from app.models import STATE_DELIVERED, STATE_IMMINENT, STATE_PENDING, Shipment
from app.state import Store


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db = root / "state.db"

        store = Store(self.db)
        store.save(Shipment(
            shipment_id="TC030ZF89", order_id="302-6054268-4901944",
            tracking_url="https://example.invalid/track", title="Solarkabel 4mm²",
            recipient="Jonas Althoff", carrier="AMZL", tracking_code="DE5713482611",
            status="out_for_delivery", stops_remaining=3,
            window_start=time(14, 0), window_end=time(18, 0),
            expected_date=date(2026, 7, 31), state=STATE_IMMINENT,
            last_seen=datetime(2026, 7, 31, 9, 0),
        ))
        store.save(Shipment(
            shipment_id="DTwBrGSbJ", order_id="302-3459175-7472324",
            tracking_url="https://example.invalid/t2", title="Adapterkabel",
            recipient="Andere Person", status="ordered",
            expected_date=date(2026, 8, 12), state=STATE_PENDING,
        ))
        store.save(Shipment(
            shipment_id="OLD", order_id="1", tracking_url="u",
            title="Schon da", state=STATE_DELIVERED,
        ))
        store.count_requests(7)
        store.record_fields({"status": True, "carrier": False})
        store.close()

        self._status = root / "status.json"
        self._status.write_text(json.dumps({
            "pakete_heute": 1, "naechste_stopps": 3, "gesamtstatus": "IMMINENT",
            "login_erforderlich": False, "gedrosselt": False, "selektoren_defekt": False,
            "letzter_abruf": "2026-07-31T09:00:00+02:00",
        }))
        self._real_status = web.STATUS_PATH
        self._real_poll = web.POLL_REQUEST_PATH
        web.STATUS_PATH = self._status
        web.POLL_REQUEST_PATH = root / ".poll-now"

        self.app = web.create_app(self.db)
        self.client = self.app.test_client()

    def tearDown(self):
        web.STATUS_PATH = self._real_status
        web.POLL_REQUEST_PATH = self._real_poll
        self._tmp.cleanup()


class TestStateEndpoint(WebTestCase):
    def test_returns_json(self):
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")

    def test_counts_exclude_delivered(self):
        data = self.client.get("/api/state").get_json()
        self.assertEqual(data["counts"]["total"], 3)
        self.assertEqual(data["counts"]["active"], 2)

    def test_shipment_fields_the_ui_needs(self):
        data = self.client.get("/api/state").get_json()
        first = next(s for s in data["shipments"] if s["shipment_id"] == "TC030ZF89")
        self.assertEqual(first["title"], "Solarkabel 4mm²")
        self.assertEqual(first["recipient"], "Jonas Althoff")
        self.assertEqual(first["stops_remaining"], 3)
        self.assertEqual(first["window_start"], "14:00:00")
        self.assertEqual(first["expected_date"], "2026-07-31")
        self.assertEqual(first["state"], STATE_IMMINENT)

    def test_sorted_by_expected_date(self):
        data = self.client.get("/api/state").get_json()
        dated = [s["expected_date"] for s in data["shipments"] if s["expected_date"]]
        self.assertEqual(dated, sorted(dated))

    def test_scheduler_status_is_passed_through(self):
        data = self.client.get("/api/state").get_json()
        self.assertEqual(data["status"]["gesamtstatus"], "IMMINENT")
        self.assertFalse(data["status"]["login_erforderlich"])

    def test_budget_and_selectors(self):
        data = self.client.get("/api/state").get_json()
        self.assertEqual(data["budget"]["amazon_used"], 7)
        self.assertEqual(data["selectors"]["status"]["ok"], 1)
        self.assertEqual(data["selectors"]["carrier"]["fail"], 1)

    def test_missing_status_file_is_not_an_error(self):
        self._status.unlink()
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], {})

    def test_corrupt_status_file_is_not_an_error(self):
        self._status.write_text("{kaputt")
        self.assertEqual(self.client.get("/api/state").get_json()["status"], {})


class TestPollEndpoint(WebTestCase):
    def test_writes_the_request_file(self):
        response = self.client.post("/api/poll")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertTrue(web.POLL_REQUEST_PATH.exists())

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get("/api/poll").status_code, 405)


class TestIndex(WebTestCase):
    def test_serves_the_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Paketbote", body)

    def test_page_uses_relative_urls_so_ingress_works(self):
        # An absolute /api/state would leave the ingress path prefix behind.
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('fetch("api/state"', body)
        self.assertNotIn('fetch("/api/state"', body)
        self.assertIn('href="browser/"', body)


if __name__ == "__main__":
    unittest.main()
