"""Tests for the UPS carrier module, against recorded response shapes."""

import unittest
from datetime import date, time
from unittest.mock import Mock, patch

from app.carriers.base import CarrierError, NotFound
from app.carriers.ups import UpsTracker, handles, parse_shipment
from app.models import (
    STATUS_DELIVERED,
    STATUS_EXCEPTION,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    STATUS_UNKNOWN,
)


def response(type_code, description="", **package):
    body = {
        "currentStatus": {
            "type": type_code,
            "code": type_code,
            "description": description,
            "location": {"address": {"city": "Köln", "countryCode": "DE"}},
        },
    }
    body.update(package)
    return {"trackResponse": {"shipment": [{"package": [body]}]}}


class TestStatusMapping(unittest.TestCase):
    def test_label_created(self):
        self.assertEqual(parse_shipment(response("M")).status, STATUS_ORDERED)

    def test_in_transit(self):
        self.assertEqual(parse_shipment(response("I")).status, STATUS_SHIPPED)

    def test_out_for_delivery(self):
        self.assertEqual(parse_shipment(response("O")).status, STATUS_OUT_FOR_DELIVERY)

    def test_delivered(self):
        self.assertEqual(parse_shipment(response("D")).status, STATUS_DELIVERED)

    def test_exception(self):
        self.assertEqual(parse_shipment(response("X")).status, STATUS_EXCEPTION)

    def test_unknown_code_is_unknown(self):
        self.assertEqual(parse_shipment(response("Z")).status, STATUS_UNKNOWN)

    def test_lowercase_type_still_maps(self):
        self.assertEqual(parse_shipment(response("d")).status, STATUS_DELIVERED)

    def test_wording_promotes_transit_to_out_for_delivery(self):
        update = parse_shipment(response("I", "Out For Delivery Today"))
        self.assertEqual(update.status, STATUS_OUT_FOR_DELIVERY)

    def test_unrelated_wording_stays_shipped(self):
        update = parse_shipment(response("I", "Arrived at Facility"))
        self.assertEqual(update.status, STATUS_SHIPPED)


class TestFields(unittest.TestCase):
    def test_delivery_date_and_window(self):
        update = parse_shipment(
            response(
                "I",
                deliveryDate=[{"type": "SDD", "date": "20260731"}],
                deliveryTime={"startTime": "090000", "endTime": "130000"},
            )
        )
        self.assertEqual(update.expected_date, date(2026, 7, 31))
        self.assertEqual(update.window_start, time(9, 0))
        self.assertEqual(update.window_end, time(13, 0))

    def test_the_last_date_wins(self):
        # UPS appends the actual delivery date after the estimate.
        update = parse_shipment(
            response(
                "D",
                deliveryDate=[
                    {"type": "SDD", "date": "20260731"},
                    {"type": "DEL", "date": "20260730"},
                ],
            )
        )
        self.assertEqual(update.expected_date, date(2026, 7, 30))

    def test_four_digit_times(self):
        update = parse_shipment(response("I", deliveryTime={"startTime": "0900"}))
        self.assertEqual(update.window_start, time(9, 0))

    def test_missing_window_is_none_not_a_crash(self):
        update = parse_shipment(response("I"))
        self.assertIsNone(update.expected_date)
        self.assertIsNone(update.window_start)
        self.assertIsNone(update.window_end)

    def test_malformed_dates_are_ignored(self):
        update = parse_shipment(response("I", deliveryDate=[{"date": "irgendwann"}]))
        self.assertIsNone(update.expected_date)

    def test_location_and_carrier(self):
        update = parse_shipment(response("I"))
        self.assertEqual(update.location, "Köln")
        self.assertEqual(update.carrier, "UPS")
        self.assertEqual(update.source, "ups")


class TestEmptyResponse(unittest.TestCase):
    def test_no_shipment_raises_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({"trackResponse": {"shipment": []}})

    def test_no_package_raises_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({"trackResponse": {"shipment": [{}]}})

    def test_missing_key_raises_not_found(self):
        with self.assertRaises(NotFound):
            parse_shipment({})


def token_response(status=200, payload=None):
    return Mock(
        status_code=status,
        ok=200 <= status < 300,
        json=Mock(return_value=payload if payload is not None
                  else {"access_token": "abc", "expires_in": 14399}),
    )


class TestToken(unittest.TestCase):
    def test_a_token_is_fetched_once_and_reused(self):
        with patch("app.carriers.ups.requests.post", return_value=token_response()) as post:
            tracker = UpsTracker("id", "secret")
            self.assertEqual(tracker.token(), "abc")
            self.assertEqual(tracker.token(), "abc")
        self.assertEqual(post.call_count, 1)

    def test_credentials_go_as_basic_auth(self):
        with patch("app.carriers.ups.requests.post", return_value=token_response()) as post:
            UpsTracker("id", "secret").token()
        self.assertEqual(post.call_args.kwargs["auth"], ("id", "secret"))
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "client_credentials")

    def test_a_rejected_secret_says_so(self):
        with patch("app.carriers.ups.requests.post", return_value=token_response(401)):
            ok, detail = UpsTracker("id", "wrong").probe()
        self.assertFalse(ok)
        self.assertIn("401", detail)

    def test_a_token_without_an_access_token_is_an_error(self):
        with patch("app.carriers.ups.requests.post",
                   return_value=token_response(200, {"scope": "public"})):
            ok, detail = UpsTracker("id", "secret").probe()
        self.assertFalse(ok)
        self.assertIn("no access token", detail)

    def test_probing_accepts_working_credentials(self):
        with patch("app.carriers.ups.requests.post", return_value=token_response()):
            ok, _ = UpsTracker("id", "secret").probe()
        self.assertTrue(ok)

    def test_without_credentials(self):
        ok, detail = UpsTracker("", "").probe()
        self.assertFalse(ok)
        self.assertIn("no credentials", detail)

    def test_half_the_credentials_is_not_enough(self):
        self.assertFalse(UpsTracker("id", "").available)
        self.assertFalse(UpsTracker("", "secret").available)

    def test_whitespace_around_pasted_credentials_is_ignored(self):
        self.assertTrue(UpsTracker("  id\n", " secret ").available)


class TestFetch(unittest.TestCase):
    def _fetch(self, status, payload=None):
        get = Mock(return_value=Mock(
            status_code=status,
            ok=200 <= status < 300,
            json=Mock(return_value=payload or response("I")),
        ))
        with patch("app.carriers.ups.requests.post", return_value=token_response()), \
             patch("app.carriers.ups.requests.get", get):
            tracker = UpsTracker("id", "secret")
            return tracker, get, tracker.fetch("1Z999AA10123456784")

    def test_a_good_answer_is_parsed(self):
        _, _, update = self._fetch(200)
        self.assertEqual(update.status, STATUS_SHIPPED)

    def test_the_token_is_sent_as_a_bearer(self):
        _, get, _ = self._fetch(200)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer abc")

    def test_an_unknown_number_raises_not_found(self):
        with self.assertRaises(NotFound):
            self._fetch(404)

    def test_a_refusal_drops_the_token_so_the_next_call_renews(self):
        get = Mock(return_value=Mock(status_code=401, ok=False))
        with patch("app.carriers.ups.requests.post", return_value=token_response()), \
             patch("app.carriers.ups.requests.get", get):
            tracker = UpsTracker("id", "secret")
            with self.assertRaises(CarrierError):
                tracker.fetch("1Z999AA10123456784")
            self.assertEqual(tracker._token, "")

    def test_every_call_is_counted_against_the_budget(self):
        store = Mock()
        get = Mock(return_value=Mock(status_code=200, ok=True,
                                     json=Mock(return_value=response("I"))))
        with patch("app.carriers.ups.requests.post", return_value=token_response()), \
             patch("app.carriers.ups.requests.get", get):
            UpsTracker("id", "secret", store).fetch("1Z999AA10123456784")
        store.count_carrier_request.assert_called_once_with("ups")


class TestHandles(unittest.TestCase):
    def test_names_this_module_answers_for(self):
        self.assertTrue(handles("UPS"))
        self.assertTrue(handles("ups"))

    def test_other_carriers_are_left_alone(self):
        for other in ("DHL", "FedEx", "Hermes", "", None):
            self.assertFalse(handles(other), other)


if __name__ == "__main__":
    unittest.main()
