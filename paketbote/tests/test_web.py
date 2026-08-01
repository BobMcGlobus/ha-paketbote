"""Tests for the add-on interface."""

import json
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

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


class TestDeliveredBucket(WebTestCase):
    """How long a delivered parcel stays out of the archive is a setting."""

    def _shipment(self, hours_ago):
        return Shipment(
            shipment_id="X", order_id="1", tracking_url="u", title="Da",
            state=STATE_DELIVERED,
            delivered_at=datetime.now() - timedelta(hours=hours_ago),
        )

    def test_freshly_delivered_is_not_archived(self):
        self.assertEqual(web.bucket_of(self._shipment(1), 72), "delivered")

    def test_past_the_window_it_moves_to_the_archive(self):
        self.assertEqual(web.bucket_of(self._shipment(80), 72), "archive")

    def test_a_longer_window_keeps_it_visible(self):
        self.assertEqual(web.bucket_of(self._shipment(80), 168), "delivered")

    def test_a_shorter_window_archives_it_sooner(self):
        self.assertEqual(web.bucket_of(self._shipment(5), 2), "archive")

    def test_a_parcel_still_on_its_way_is_current(self):
        underway = Shipment(shipment_id="Y", order_id="2", tracking_url="u", title="Kommt")
        self.assertEqual(web.bucket_of(underway, 72), "current")

    def test_every_shipment_is_given_a_bucket(self):
        data = self.client.get("/api/state").get_json()
        for shipment in data["shipments"]:
            self.assertIn(shipment["bucket"], ("current", "delivered", "archive"))


class TestPollEndpoint(WebTestCase):
    def test_writes_the_request_file(self):
        response = self.client.post("/api/poll")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertTrue(web.POLL_REQUEST_PATH.exists())

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get("/api/poll").status_code, 405)


class TestManualShipments(WebTestCase):
    def _add(self, **payload):
        body = {"tracking_code": "00340434161094042557", "carrier": "dhl"}
        body.update(payload)
        return self.client.post("/api/shipments", json=body)

    def test_creates_a_shipment(self):
        response = self._add(title="Geschenk", recipient="Jonas")
        self.assertEqual(response.status_code, 201)
        shipment_id = response.get_json()["shipment_id"]

        store = Store(self.db)
        try:
            found = {s.shipment_id: s for s in store.all_shipments()}[shipment_id]
        finally:
            store.close()
        self.assertEqual(found.source, "manual")
        self.assertEqual(found.carrier, "DHL")
        self.assertEqual(found.tracking_code, "00340434161094042557")
        self.assertEqual(found.title, "Geschenk")
        self.assertIn("00340434161094042557", found.tracking_url)

    def test_falls_back_to_the_number_as_a_label(self):
        response = self._add(title="")
        store = Store(self.db)
        try:
            found = {s.shipment_id: s for s in store.all_shipments()}[response.get_json()["shipment_id"]]
        finally:
            store.close()
        self.assertEqual(found.title, "00340434161094042557")

    def test_tracking_number_is_required(self):
        response = self._add(tracking_code="   ")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "tracking_code_required")

    def test_carrier_must_be_known(self):
        response = self._add(carrier="rohrpost")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "unknown_carrier")

    def test_carrier_may_be_given_by_name(self):
        self.assertEqual(self._add(carrier="Hermes").status_code, 201)

    def test_adding_asks_for_a_poll(self):
        web.POLL_REQUEST_PATH.unlink(missing_ok=True)
        self._add()
        self.assertTrue(web.POLL_REQUEST_PATH.exists())

    def test_appears_in_the_state_endpoint(self):
        self._add(recipient="Jonas")
        data = self.client.get("/api/state").get_json()
        manual = [s for s in data["shipments"] if s["source"] == "manual"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["recipient"], "Jonas")

    def test_delete_removes_it(self):
        shipment_id = self._add().get_json()["shipment_id"]
        self.assertEqual(self.client.delete(f"/api/shipments/{shipment_id}").status_code, 200)
        data = self.client.get("/api/state").get_json()
        self.assertFalse([s for s in data["shipments"] if s["source"] == "manual"])

    def test_carrier_choices_are_offered(self):
        data = self.client.get("/api/state").get_json()
        names = [c["name"] for c in data["carriers"]]
        self.assertIn("DHL", names)
        self.assertIn("Hermes", names)
        # DHL is the only one queried automatically so far.
        self.assertTrue(next(c for c in data["carriers"] if c["name"] == "DHL")["automatic"])


class TestIndex(WebTestCase):
    def test_serves_the_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Paketbote", body)

    def test_offers_both_languages(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("Sendung hinzufügen", body)
        self.assertIn("Add a shipment", body)

    def test_page_uses_relative_urls_so_ingress_works(self):
        # An absolute /api/state would leave the ingress path prefix behind.
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('fetch("api/state"', body)
        self.assertNotIn('fetch("/api/state"', body)
        self.assertIn('href="browser/"', body)
        self.assertIn('fetch("api/shipments"', body)


class TestSettings(WebTestCase):
    def setUp(self):
        super().setUp()
        from app import settings as settings_module
        self._real_settings = settings_module.SETTINGS_PATH
        settings_module.SETTINGS_PATH = Path(self._tmp.name) / "settings.json"
        self.settings_module = settings_module

    def tearDown(self):
        self.settings_module.SETTINGS_PATH = self._real_settings
        super().tearDown()

    def test_schema_is_grouped(self):
        data = self.client.get("/api/settings").get_json()
        groups = {f["group"] for f in data["schema"]}
        self.assertIn("polling", groups)
        self.assertIn("carriers", groups)
        self.assertTrue(set(data["groups"]) >= groups)

    def test_saving_changes_a_value(self):
        self.client.post("/api/settings", json={"poll_idle_minutes": 25})
        self.assertEqual(self.client.get("/api/settings").get_json()["values"]["poll_idle_minutes"], 25)

    def test_values_are_clamped(self):
        self.client.post("/api/settings", json={"jitter_percent": 5000})
        self.assertEqual(self.client.get("/api/settings").get_json()["values"]["jitter_percent"], 50)

    def test_secrets_are_never_sent_back(self):
        self.client.post("/api/settings", json={"dhl_api_key": "geheim"})
        data = self.client.get("/api/settings").get_json()
        self.assertEqual(data["values"]["dhl_api_key"], "")
        self.assertTrue(data["secrets"]["dhl_api_key"])

    def test_an_empty_password_field_keeps_the_stored_key(self):
        # The settings form posts every field; without this a save would wipe
        # a key the user never touched.
        self.client.post("/api/settings", json={"dhl_api_key": "geheim"})
        self.client.post("/api/settings", json={"dhl_api_key": "", "poll_idle_minutes": 30})
        stored = json.loads(self.settings_module.SETTINGS_PATH.read_text())
        self.assertEqual(stored["dhl_api_key"], "geheim")

    def test_reset_keeps_keys_and_filters(self):
        self.client.post("/api/settings", json={
            "dhl_api_key": "geheim", "poll_idle_minutes": 25,
            "hidden_recipients": ["eltern"],
        })
        self.client.post("/api/settings/reset")
        stored = json.loads(self.settings_module.SETTINGS_PATH.read_text())
        self.assertEqual(stored["dhl_api_key"], "geheim")
        self.assertEqual(stored["hidden_recipients"], ["eltern"])
        self.assertNotIn("poll_idle_minutes", stored)

    def test_carriers_that_only_have_an_api_report_a_missing_key(self):
        for carrier in ("ups", "fedex"):
            response = self.client.post(f"/api/test/{carrier}")
            self.assertEqual(response.status_code, 200, carrier)
            data = response.get_json()
            self.assertFalse(data["ok"], carrier)
            self.assertEqual(data["reason"], "no_key", carrier)

    def test_carriers_readable_from_the_web_are_tested_without_a_key(self):
        # No key configured, so this must reach the website tier — and it must
        # not touch the network from a test.
        with patch("app.carriers.scraping.requests.get",
                   return_value=Mock(status_code=200, ok=True)) as get:
            for carrier in ("dhl", "hermes", "dpd"):
                data = self.client.post(f"/api/test/{carrier}").get_json()
                self.assertTrue(data["ok"], carrier)
                self.assertIn("web", data["reason"], carrier)
        self.assertTrue(get.called)

    def test_a_carrier_we_cannot_ask_is_a_404(self):
        response = self.client.post("/api/test/gls")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["ok"])

    def test_hidden_recipients_round_trip(self):
        self.client.post("/api/settings", json={"hidden_recipients": ["eltern althoff"]})
        self.assertEqual(
            self.client.get("/api/state").get_json()["hidden_recipients"], ["eltern althoff"]
        )


if __name__ == "__main__":
    unittest.main()
