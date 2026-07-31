"""Tests for the FedEx carrier module, against recorded response shapes."""

import unittest
from datetime import date, time
from unittest.mock import Mock, patch

from app.carriers.base import CarrierError, NotFound
from app.carriers.fedex import FedexTracker, handles, parse_shipment
from app.models import (
    STATUS_DELIVERED,
    STATUS_EXCEPTION,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    STATUS_UNKNOWN,
)


def response(derived_code, description="", **track):
    result = {
        "latestStatusDetail": {
            "code": derived_code,
            "derivedCode": derived_code,
            "statusByLocale": description,
            "description": description,
            "scanLocation": {"city": "Frankfurt", "countryCode": "DE"},
        },
    }
    result.update(track)
    return {"output": {"completeTrackResults": [{"trackResults": [result]}]}}


class TestStatusMapping(unittest.TestCase):
    def test_order_created(self):
        self.assertEqual(parse_shipment(response("OC")).status, STATUS_ORDERED)

    def test_in_transit(self):
        self.assertEqual(parse_shipment(response("IT")).status, STATUS_SHIPPED)

    def test_picked_up(self):
        self.assertEqual(parse_shipment(response("PU")).status, STATUS_SHIPPED)

    def test_out_for_delivery(self):
        self.assertEqual(parse_shipment(response("OD")).status, STATUS_OUT_FOR_DELIVERY)

    def test_delivered(self):
        self.assertEqual(parse_shipment(response("DL")).status, STATUS_DELIVERED)

    def test_delivery_exception(self):
        self.assertEqual(parse_shipment(response("DE")).status, STATUS_EXCEPTION)

    def test_cancelled(self):
        self.assertEqual(parse_shipment(response("CA")).status, STATUS_EXCEPTION)

    def test_unknown_code_is_unknown(self):
        self.assertEqual(parse_shipment(response("ZZ")).status, STATUS_UNKNOWN)

    def test_lowercase_code_still_maps(self):
        self.assertEqual(parse_shipment(response("dl")).status, STATUS_DELIVERED)


class TestFields(unittest.TestCase):
    def test_estimated_delivery_date(self):
        update = parse_shipment(
            response("IT", dateAndTimes=[
                {"type": "ACTUAL_PICKUP", "dateTime": "2026-07-29T10:00:00+02:00"},
                {"type": "ESTIMATED_DELIVERY", "dateTime": "2026-07-31T00:00:00+02:00"},
            ])
        )
        self.assertEqual(update.expected_date, date(2026, 7, 31))

    def test_the_actual_delivery_beats_the_estimate(self):
        update = parse_shipment(
            response("DL", dateAndTimes=[
                {"type": "ESTIMATED_DELIVERY", "dateTime": "2026-07-31T00:00:00+02:00"},
                {"type": "ACTUAL_DELIVERY", "dateTime": "2026-07-30T11:20:00+02:00"},
            ])
        )
        self.assertEqual(update.expected_date, date(2026, 7, 30))

    def test_an_unrelated_date_type_is_not_used(self):
        update = parse_shipment(
            response("IT", dateAndTimes=[{"type": "SHIP", "dateTime": "2026-07-28T08:00:00"}])
        )
        self.assertIsNone(update.expected_date)

    def test_delivery_window(self):
        update = parse_shipment(
            response("IT", estimatedDeliveryTimeWindow={"window": {
                "begins": "2026-07-31T10:00:00+02:00",
                "ends": "2026-07-31T14:00:00+02:00",
            }})
        )
        self.assertEqual(update.window_start, time(10, 0))
        self.assertEqual(update.window_end, time(14, 0))

    def test_bare_dates_are_accepted(self):
        update = parse_shipment(
            response("IT", dateAndTimes=[{"type": "ESTIMATED_DELIVERY", "dateTime": "2026-07-31"}])
        )
        self.assertEqual(update.expected_date, date(2026, 7, 31))

    def test_missing_window_is_none_not_a_crash(self):
        update = parse_shipment(response("IT"))
        self.assertIsNone(update.expected_date)
        self.assertIsNone(update.window_start)

    def test_location_and_carrier(self):
        update = parse_shipment(response("IT"))
        self.assertEqual(update.location, "Frankfurt")
        self.assertEqual(update.carrier, "FedEx")
        self.assertEqual(update.source, "fedex")


class TestEmptyResponse(unittest.TestCase):
    def test_no_results_raises_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({"output": {"completeTrackResults": []}})

    def test_no_track_results_raises_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({"output": {"completeTrackResults": [{"trackResults": []}]}})

    def test_missing_key_raises_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({})

    def test_a_per_result_error_is_not_found_not_a_crash(self):
        # FedEx answers HTTP 200 and puts "we don't know this number" inside.
        payload = {"output": {"completeTrackResults": [{"trackResults": [
            {"error": {"code": "TRACKING.TRACKINGNUMBER.NOTFOUND",
                       "message": "Tracking number cannot be found"}}
        ]}]}}
        with self.assertRaises(NotFound):
            parse_shipment(payload)


def token_response(status=200, payload=None):
    return Mock(
        status_code=status,
        ok=200 <= status < 300,
        json=Mock(return_value=payload if payload is not None
                  else {"access_token": "abc", "expires_in": 3599}),
    )


class TestToken(unittest.TestCase):
    def test_a_token_is_fetched_once_and_reused(self):
        with patch("app.carriers.fedex.requests.post", return_value=token_response()) as post:
            tracker = FedexTracker("id", "secret")
            tracker.token()
            tracker.token()
        self.assertEqual(post.call_count, 1)

    def test_credentials_go_in_the_body(self):
        with patch("app.carriers.fedex.requests.post", return_value=token_response()) as post:
            FedexTracker("id", "secret").token()
        data = post.call_args.kwargs["data"]
        self.assertEqual(data["client_id"], "id")
        self.assertEqual(data["client_secret"], "secret")
        self.assertEqual(data["grant_type"], "client_credentials")

    def test_a_rejected_secret_says_so(self):
        with patch("app.carriers.fedex.requests.post", return_value=token_response(401)):
            ok, detail = FedexTracker("id", "wrong").probe()
        self.assertFalse(ok)
        self.assertIn("401", detail)

    def test_probing_accepts_working_credentials(self):
        with patch("app.carriers.fedex.requests.post", return_value=token_response()):
            ok, _ = FedexTracker("id", "secret").probe()
        self.assertTrue(ok)

    def test_without_credentials(self):
        ok, detail = FedexTracker("", "").probe()
        self.assertFalse(ok)
        self.assertIn("no credentials", detail)


class TestFetch(unittest.TestCase):
    def _patched(self, status, payload=None):
        post = Mock(side_effect=[
            token_response(),
            Mock(status_code=status, ok=200 <= status < 300,
                 json=Mock(return_value=payload or response("IT"))),
        ])
        return post, patch("app.carriers.fedex.requests.post", post)

    def test_a_good_answer_is_parsed(self):
        post, patched = self._patched(200)
        with patched:
            update = FedexTracker("id", "secret").fetch("781820534123")
        self.assertEqual(update.status, STATUS_SHIPPED)

    def test_the_tracking_number_goes_in_the_body(self):
        post, patched = self._patched(200)
        with patched:
            FedexTracker("id", "secret").fetch("781820534123")
        body = post.call_args.kwargs["json"]
        self.assertEqual(
            body["trackingInfo"][0]["trackingNumberInfo"]["trackingNumber"], "781820534123"
        )
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer abc")

    def test_an_unknown_number_raises_not_found(self):
        _, patched = self._patched(404)
        with patched, self.assertRaises(NotFound):
            FedexTracker("id", "secret").fetch("781820534123")

    def test_a_refusal_drops_the_token_so_the_next_call_renews(self):
        _, patched = self._patched(403)
        with patched:
            tracker = FedexTracker("id", "secret")
            with self.assertRaises(CarrierError):
                tracker.fetch("781820534123")
            self.assertEqual(tracker._token, "")

    def test_every_call_is_counted_against_the_budget(self):
        store = Mock()
        _, patched = self._patched(200)
        with patched:
            FedexTracker("id", "secret", store).fetch("781820534123")
        store.count_carrier_request.assert_called_once_with("fedex")


class TestHandles(unittest.TestCase):
    def test_names_this_module_answers_for(self):
        self.assertTrue(handles("FedEx"))
        self.assertTrue(handles("fedex"))
        self.assertTrue(handles("TNT"))

    def test_other_carriers_are_left_alone(self):
        for other in ("DHL", "UPS", "Hermes", "", None):
            self.assertFalse(handles(other), other)


if __name__ == "__main__":
    unittest.main()
