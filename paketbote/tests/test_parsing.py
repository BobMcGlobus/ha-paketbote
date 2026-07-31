"""Tests for Amazon's delivery wording, in both languages."""

import unittest
from datetime import date, time

from app.models import (
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
)
from app.parsing import (
    detect_carrier,
    parse_expected_date,
    parse_stops,
    parse_window,
    status_from_label,
)

TODAY = date(2026, 7, 30)


class TestExpectedDate(unittest.TestCase):
    def test_tomorrow_english(self):
        self.assertEqual(parse_expected_date("Arriving tomorrow", TODAY), date(2026, 7, 31))

    def test_tomorrow_german(self):
        self.assertEqual(parse_expected_date("Ankunft morgen", TODAY), date(2026, 7, 31))

    def test_today(self):
        self.assertEqual(parse_expected_date("Arriving today", TODAY), TODAY)
        self.assertEqual(parse_expected_date("Kommt heute", TODAY), TODAY)

    def test_range_takes_the_earliest_day(self):
        # "Arriving 12 August - 13 August": the 12th is when watching starts.
        self.assertEqual(
            parse_expected_date("Arriving 12 August - 13 August", TODAY), date(2026, 8, 12)
        )

    def test_german_day_month_with_dot(self):
        self.assertEqual(parse_expected_date("Ankunft Freitag, 1. August", TODAY), date(2026, 8, 1))

    def test_month_first_abbreviated(self):
        self.assertEqual(parse_expected_date("Get it Tomorrow, Jul 31", TODAY), date(2026, 7, 31))

    def test_german_month_name(self):
        self.assertEqual(parse_expected_date("Zugestellt am 28. Juli", TODAY), date(2026, 7, 28))

    def test_year_rolls_over_for_january(self):
        # Read on 30 December, "3 January" means next year.
        self.assertEqual(
            parse_expected_date("Arriving 3 January", date(2026, 12, 30)), date(2027, 1, 3)
        )

    def test_nonsense_yields_none(self):
        self.assertIsNone(parse_expected_date("Buy it again", TODAY))
        self.assertIsNone(parse_expected_date("", TODAY))

    def test_impossible_date_is_ignored(self):
        self.assertIsNone(parse_expected_date("Arriving 31 February", TODAY))


class TestWindow(unittest.TestCase):
    def test_german_between(self):
        self.assertEqual(
            parse_window("Zwischen 14:00 und 18:00"), (time(14, 0), time(18, 0))
        )

    def test_german_bare_hours(self):
        self.assertEqual(parse_window("zwischen 9 und 13 Uhr"), (time(9, 0), time(13, 0)))

    def test_dash_range(self):
        self.assertEqual(parse_window("14:00 - 18:00"), (time(14, 0), time(18, 0)))

    def test_am_pm(self):
        self.assertEqual(parse_window("between 2pm and 6pm"), (time(14, 0), time(18, 0)))

    def test_noon_and_midnight_am_pm(self):
        self.assertEqual(parse_window("between 12am and 12pm"), (time(0, 0), time(12, 0)))

    def test_the_shapes_amazon_actually_uses(self):
        # Taken from real tracker pages. The am/pm sits between the minutes and
        # the dash, which the earlier patterns did not survive.
        self.assertEqual(
            parse_window("Arriving today 5:30 pm - 8:30 pm"), (time(17, 30), time(20, 30)))
        self.assertEqual(
            parse_window("Now arriving today 3:15 pm - 5:15 pm"), (time(15, 15), time(17, 15)))
        self.assertEqual(
            parse_window("Arriving tomorrow 7 am – 1 pm"), (time(7, 0), time(13, 0)))
        self.assertEqual(
            parse_window("Zustellung heute 15:15 - 17:15"), (time(15, 15), time(17, 15)))

    def test_a_trailing_marker_applies_to_both_halves(self):
        self.assertEqual(parse_window("5:30 - 8:30 pm"), (time(17, 30), time(20, 30)))

    def test_date_ranges_are_not_windows(self):
        for text in ("Arriving 12 August - 13 August", "Arriving 13 August - 31 August",
                     "Expected by 12 August", "Delivered 30 July"):
            self.assertIsNone(parse_window(text), text)

    def test_unrelated_numbers_are_not_a_window(self):
        self.assertIsNone(parse_window("Packung mit 3 Gläsern x 250 ml"))

    def test_no_window(self):
        self.assertIsNone(parse_window("Arriving tomorrow"))
        self.assertIsNone(parse_window(""))


class TestStops(unittest.TestCase):
    def test_english(self):
        self.assertEqual(parse_stops("6 stops away"), 6)
        self.assertEqual(parse_stops("Your package is 12 stops away"), 12)

    def test_german(self):
        self.assertEqual(parse_stops("Noch 6 Stopps"), 6)
        self.assertEqual(parse_stops("3 Haltestellen entfernt"), 3)

    def test_zero_is_a_real_answer(self):
        self.assertEqual(parse_stops("0 stops away"), 0)

    def test_the_map_callout_in_both_languages(self):
        # This is where the count actually lives: Amazon's own JSON, rendered
        # into a map bubble later.
        self.assertEqual(parse_stops("2 stops away"), 2)
        self.assertEqual(parse_stops("2 Stopps entfernt"), 2)

    def test_unrelated_numbers_are_not_stops(self):
        # The plan is explicit: never guess a stop count.
        self.assertIsNone(parse_stops("€16.95"))
        self.assertIsNone(parse_stops("Arriving 12 August - 13 August"))
        self.assertIsNone(parse_stops("4.6 out of 5 stars"))


class TestStatusLabels(unittest.TestCase):
    def test_english_labels(self):
        self.assertEqual(status_from_label("Ordered"), STATUS_ORDERED)
        self.assertEqual(status_from_label("Dispatched"), STATUS_SHIPPED)
        self.assertEqual(status_from_label("Out for delivery"), STATUS_OUT_FOR_DELIVERY)
        self.assertEqual(status_from_label("Delivered"), STATUS_DELIVERED)

    def test_german_labels(self):
        self.assertEqual(status_from_label("Bestellt"), STATUS_ORDERED)
        self.assertEqual(status_from_label("Versandt"), STATUS_SHIPPED)
        self.assertEqual(status_from_label("Zugestellt"), STATUS_DELIVERED)

    def test_unknown_label(self):
        self.assertIsNone(status_from_label("Buy it again"))


class TestCarrier(unittest.TestCase):
    def test_named_carriers(self):
        self.assertEqual(detect_carrier("Delivered by DHL"), "DHL")
        self.assertEqual(detect_carrier("Zugestellt von Amazon"), "AMZL")

    def test_no_carrier(self):
        self.assertIsNone(detect_carrier("Tracking ID: DE5713482611"))


if __name__ == "__main__":
    unittest.main()
