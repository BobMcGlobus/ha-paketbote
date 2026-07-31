"""Tests for reading DPD off their tracking page, against the real markup."""

import unittest
from datetime import date, time
from unittest.mock import Mock, patch

from app.carriers.base import NotFound
from app.carriers.dpd import DpdTracker, handles, parse_page
from app.models import (
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
)

STAGES = ("Start", "OnTheRoad", "DeliveryDepot", "CarLoad", "Delivered")


def page(stage, dates=None, status="Paket unterwegs", extra=""):
    """The shape my.dpd.de actually serves, trimmed to what is read."""
    dates = dates or {}
    rows = "".join(
        f'<span id="ContentPlaceHolder1_labStatus{name}" class="labSub13">x</span>'
        f'<span id="ContentPlaceHolder1_labStatus{name}Date" class="labSub13 lSBold">'
        f'{dates.get(name, "")}</span>'
        for name in STAGES
    )
    icon = (
        f'<img id="ContentPlaceHolder1_imgParcelStatus" src="images/status_{stage}.svg" />'
        if stage else ""
    )
    return (
        f'<html><body>{icon}'
        f'<span id="ContentPlaceHolder1_repParcelList_labDeliveryStatus_0">{status}</span>'
        f'{rows}{extra}</body></html>'
    )


TODAY = date(2026, 7, 31)


class TestStageMapping(unittest.TestCase):
    """The stage is an icon number, so it does not depend on the language."""

    def test_order_submitted(self):
        self.assertEqual(parse_page(page(1), TODAY).status, STATUS_ORDERED)

    def test_on_its_way(self):
        for stage in (2, 3):
            self.assertEqual(parse_page(page(stage), TODAY).status, STATUS_SHIPPED, stage)

    def test_out_for_delivery(self):
        self.assertEqual(parse_page(page(4), TODAY).status, STATUS_OUT_FOR_DELIVERY)

    def test_delivered(self):
        self.assertEqual(parse_page(page(5), TODAY).status, STATUS_DELIVERED)

    def test_the_dated_stages_stand_in_when_the_icon_is_missing(self):
        update = parse_page(page(None, {"Start": "30.07.", "OnTheRoad": "31.07."}), TODAY)
        self.assertEqual(update.status, STATUS_SHIPPED)

    def test_a_page_without_any_status_is_not_found(self):
        with self.assertRaises(NotFound):
            parse_page("<html><body>Cookie-Hinweis</body></html>", TODAY)


class TestRecordedPages(unittest.TestCase):
    """Both shapes seen on real parcels."""

    def test_freshly_announced(self):
        update = parse_page(
            page(1, {"Start": "31.07."}, "Auftragsdaten übermittelt"), TODAY
        )
        self.assertEqual(update.status, STATUS_ORDERED)
        self.assertEqual(update.description, "Auftragsdaten übermittelt")

    def test_in_transit(self):
        update = parse_page(
            page(2, {"Start": "30.07.", "OnTheRoad": "31.07."}, "Paket unterwegs"), TODAY
        )
        self.assertEqual(update.status, STATUS_SHIPPED)
        self.assertEqual(update.description, "Paket unterwegs")


class TestDates(unittest.TestCase):
    def test_a_stage_date_before_the_van_is_not_an_arrival_date(self):
        # "Paket unterwegs, 30.07." says when it left, not when it arrives.
        update = parse_page(page(2, {"Start": "30.07.", "OnTheRoad": "30.07."}), TODAY)
        self.assertIsNone(update.expected_date)

    def test_out_for_delivery_means_the_date_is_the_delivery_date(self):
        update = parse_page(page(4, {"Start": "30.07.", "CarLoad": "31.07."}), TODAY)
        self.assertEqual(update.expected_date, date(2026, 7, 31))

    def test_delivered_takes_the_delivery_date(self):
        update = parse_page(page(5, {"Start": "29.07.", "Delivered": "30.07."}), TODAY)
        self.assertEqual(update.expected_date, date(2026, 7, 30))

    def test_a_date_in_the_new_year_belongs_to_the_year_before(self):
        # Parcel delivered 30.12., read on 2 January.
        update = parse_page(page(5, {"Delivered": "30.12."}), date(2027, 1, 2))
        self.assertEqual(update.expected_date, date(2026, 12, 30))

    def test_nonsense_dates_are_ignored(self):
        update = parse_page(page(4, {"CarLoad": "irgendwann"}), TODAY)
        self.assertIsNone(update.expected_date)

    def test_an_impossible_date_is_ignored(self):
        update = parse_page(page(4, {"CarLoad": "31.02."}), TODAY)
        self.assertIsNone(update.expected_date)


class TestWindow(unittest.TestCase):
    def test_an_hour_slot_on_the_page_is_read(self):
        update = parse_page(
            page(4, {"CarLoad": "31.07."}, extra="<p>Zustellung zwischen 14 und 15 Uhr</p>"),
            TODAY,
        )
        self.assertEqual(update.window_start, time(14, 0))
        self.assertEqual(update.window_end, time(15, 0))

    def test_no_slot_is_none(self):
        self.assertIsNone(parse_page(page(2), TODAY).window_start)

    def test_times_inside_scripts_are_not_mistaken_for_a_window(self):
        update = parse_page(
            page(2, extra="<script>var t='10:00 - 12:00';</script>"), TODAY
        )
        self.assertIsNone(update.window_start)


class TestTracker(unittest.TestCase):
    def test_it_needs_no_credentials(self):
        self.assertTrue(DpdTracker().available)

    def test_the_postcode_is_passed_on_when_known(self):
        get = Mock(return_value=Mock(status_code=200, ok=True, text=page(2)))
        with patch("app.carriers.scraping.requests.get", get):
            DpdTracker().fetch("01415129595439", "59065")
        self.assertEqual(get.call_args.kwargs["params"]["zip"], "59065")

    def test_it_works_without_a_postcode_too(self):
        get = Mock(return_value=Mock(status_code=200, ok=True, text=page(2)))
        with patch("app.carriers.scraping.requests.get", get):
            update = DpdTracker().fetch("01415129595439")
        self.assertNotIn("zip", get.call_args.kwargs["params"])
        self.assertEqual(update.status, STATUS_SHIPPED)

    def test_web_reads_are_counted_in_their_own_bucket(self):
        store = Mock()
        with patch("app.carriers.scraping.requests.get",
                   return_value=Mock(status_code=200, ok=True, text=page(2))):
            DpdTracker(store).fetch("01415129595439")
        store.count_carrier_request.assert_called_once_with("dpd_web")


class TestHandles(unittest.TestCase):
    def test_names_this_module_answers_for(self):
        self.assertTrue(handles("DPD"))
        self.assertTrue(handles("dpd"))

    def test_other_carriers_are_left_alone(self):
        for other in ("DHL", "GLS", "Hermes", "", None):
            self.assertFalse(handles(other), other)


if __name__ == "__main__":
    unittest.main()
