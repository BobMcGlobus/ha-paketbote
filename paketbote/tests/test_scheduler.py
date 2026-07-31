"""Tests for the polling ladder, including the plan's 24 h budget simulation."""

import random
import unittest
from datetime import date, datetime, time, timedelta

from app.config import Config
from app.models import (
    STATE_APPROACHING,
    STATE_DELIVERED,
    STATE_IDLE,
    STATE_IMMINENT,
    STATE_PENDING,
    STATE_WINDOW,
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    Shipment,
)
from app.scheduler import (
    affordable_interval,
    apply_jitter,
    compute_state,
    in_quiet_hours,
    most_urgent,
    next_interval_minutes,
    summarise,
)

CONFIG = Config()
TODAY = date(2026, 7, 30)


def at(hour, minute=0):
    return datetime(2026, 7, 30, hour, minute)


def state(**kwargs):
    base = dict(
        status=STATUS_SHIPPED,
        expected_date=TODAY,
        window_start=None,
        window_end=None,
        stops_remaining=None,
        now=at(12),
        config=CONFIG,
    )
    base.update(kwargs)
    return compute_state(**base)


class TestComputeState(unittest.TestCase):
    def test_delivered_wins_over_everything(self):
        self.assertEqual(
            state(status=STATUS_DELIVERED, stops_remaining=0, window_start=time(8)),
            STATE_DELIVERED,
        )

    def test_future_delivery_is_idle(self):
        self.assertEqual(state(expected_date=date(2026, 8, 12)), STATE_IDLE)

    def test_no_date_and_not_moving_is_idle(self):
        self.assertEqual(state(expected_date=None, status=STATUS_ORDERED), STATE_IDLE)

    def test_due_today_before_the_window_is_pending(self):
        self.assertEqual(state(window_start=time(14), now=at(9)), STATE_PENDING)

    def test_window_opens_on_time(self):
        self.assertEqual(state(window_start=time(14), now=at(14, 1)), STATE_WINDOW)

    def test_out_for_delivery_counts_as_an_open_window_without_times(self):
        # amazon.de usually names no window at all; the milestone is the signal.
        self.assertEqual(
            state(status=STATUS_OUT_FOR_DELIVERY, window_start=None), STATE_WINDOW
        )

    def test_approaching_below_threshold(self):
        self.assertEqual(
            state(status=STATUS_OUT_FOR_DELIVERY, stops_remaining=6), STATE_APPROACHING
        )

    def test_at_the_threshold_is_not_yet_approaching(self):
        self.assertEqual(
            state(status=STATUS_OUT_FOR_DELIVERY, stops_remaining=7), STATE_WINDOW
        )

    def test_imminent_below_threshold(self):
        self.assertEqual(
            state(status=STATUS_OUT_FOR_DELIVERY, stops_remaining=1), STATE_IMMINENT
        )

    def test_missing_stop_count_stops_the_ladder_at_window(self):
        # The open question: if amazon.de never shows stops, this is the top rung.
        self.assertEqual(
            state(status=STATUS_OUT_FOR_DELIVERY, stops_remaining=None), STATE_WINDOW
        )

    def test_backwards_transition_is_allowed(self):
        # Amazon revises stop counts upwards; state must follow back down.
        forward = state(status=STATUS_OUT_FOR_DELIVERY, stops_remaining=1)
        backward = state(status=STATUS_OUT_FOR_DELIVERY, stops_remaining=9)
        self.assertEqual(forward, STATE_IMMINENT)
        self.assertEqual(backward, STATE_WINDOW)

    def test_an_hour_past_the_window_stops_polling(self):
        self.assertEqual(
            state(window_start=time(14), window_end=time(16), now=at(17, 1)), STATE_DELIVERED
        )

    def test_inside_the_grace_period_still_polls(self):
        self.assertEqual(
            state(window_start=time(14), window_end=time(16), now=at(16, 30)), STATE_WINDOW
        )

    def test_without_a_window_the_delivery_day_still_ends(self):
        # amazon.de usually names no window. Without an implicit end, a package
        # stuck at one stop would be polled every minute all evening.
        self.assertEqual(
            state(status=STATUS_OUT_FOR_DELIVERY, stops_remaining=1, window_end=None, now=at(21)),
            STATE_IMMINENT,
        )
        self.assertEqual(
            state(status=STATUS_OUT_FOR_DELIVERY, stops_remaining=1, window_end=None, now=at(23, 30)),
            STATE_DELIVERED,
        )

    def test_disabled_quiet_hours_do_not_end_the_day_early(self):
        config = Config(quiet_hours_start=0, quiet_hours_end=0)
        self.assertEqual(
            compute_state(
                status=STATUS_OUT_FOR_DELIVERY, expected_date=TODAY, window_start=None,
                window_end=None, stops_remaining=1, now=at(20), config=config,
            ),
            STATE_IMMINENT,
        )


class TestQuietHours(unittest.TestCase):
    def test_wraps_around_midnight(self):
        self.assertTrue(in_quiet_hours(at(23), CONFIG))
        self.assertTrue(in_quiet_hours(at(3), CONFIG))
        self.assertFalse(in_quiet_hours(at(12), CONFIG))

    def test_boundaries(self):
        self.assertTrue(in_quiet_hours(at(22), CONFIG))
        self.assertFalse(in_quiet_hours(at(6), CONFIG))

    def test_equal_bounds_disable_quiet_hours(self):
        config = Config(quiet_hours_start=0, quiet_hours_end=0)
        self.assertFalse(in_quiet_hours(at(3), config))

    def test_night_holds_the_idle_interval_even_when_imminent(self):
        minutes = next_interval_minutes([STATE_IMMINENT], at(3), CONFIG, random.Random(1))
        self.assertGreater(minutes, CONFIG.poll_idle_minutes * 0.5)


class TestIntervals(unittest.TestCase):
    def test_the_most_urgent_shipment_sets_the_pace(self):
        rng = random.Random(0)
        minutes = next_interval_minutes([STATE_IDLE, STATE_IMMINENT], at(15), CONFIG, rng)
        self.assertLess(minutes, CONFIG.poll_pending_minutes)

    def test_no_shipments_means_idle(self):
        minutes = next_interval_minutes([], at(15), Config(jitter_percent=0))
        self.assertEqual(minutes, Config().poll_idle_minutes)

    def test_jitter_stays_inside_the_band(self):
        rng = random.Random(7)
        for _ in range(200):
            value = apply_jitter(10, 20, rng)
            self.assertGreaterEqual(value, 8.0)
            self.assertLessEqual(value, 12.0)

    def test_zero_jitter_is_exact(self):
        self.assertEqual(apply_jitter(10, 0), 10.0)

    def test_most_urgent_ordering(self):
        self.assertEqual(most_urgent([STATE_IDLE, STATE_WINDOW, STATE_PENDING]), STATE_WINDOW)
        self.assertEqual(most_urgent([]), STATE_IDLE)


class TestSummary(unittest.TestCase):
    def _shipment(self, **kwargs):
        base = dict(
            shipment_id="A",
            order_id="1",
            tracking_url="u",
            expected_date=TODAY,
            state=STATE_WINDOW,
        )
        base.update(kwargs)
        return Shipment(**base)

    def test_counts_and_minimum_stops(self):
        shipments = [
            self._shipment(shipment_id="A", stops_remaining=9),
            self._shipment(shipment_id="B", stops_remaining=3),
            self._shipment(shipment_id="C", state=STATE_DELIVERED, stops_remaining=0),
        ]
        summary = summarise(shipments, at(15), CONFIG)
        self.assertEqual(summary["pakete_heute"], 2)
        self.assertEqual(summary["pakete_aktiv"], 2)
        self.assertEqual(summary["naechste_stopps"], 3)
        self.assertTrue(summary["zustellfenster_aktiv"])
        self.assertFalse(summary["zustellung_unmittelbar"])

    def test_imminent_raises_the_flag(self):
        summary = summarise([self._shipment(state=STATE_IMMINENT)], at(15), CONFIG)
        self.assertTrue(summary["zustellung_unmittelbar"])
        self.assertEqual(summary["gesamtstatus"], STATE_IMMINENT)

    def test_empty_is_idle_not_a_crash(self):
        summary = summarise([], at(15), CONFIG)
        self.assertEqual(summary["gesamtstatus"], STATE_IDLE)
        self.assertIsNone(summary["naechste_stopps"])
        self.assertIsNone(summary["naechstes_fenster"])


class TestDayBudget(unittest.TestCase):
    """The plan's acceptance criterion: a compressed 24 h run lands near 67."""

    def _simulate(self, *, with_stops: bool, ever_delivered: bool):
        """Walk a whole day, taking each interval the scheduler asks for.

        Shaped after the plan's budget table: Amazon only confirms the delivery
        date in the morning, the van goes out at 14:00, and the promised window
        closes at 16:00.
        """
        config = Config(jitter_percent=0)
        now = datetime(2026, 7, 30, 0, 0)
        end = now + timedelta(days=1)
        out_for_delivery_at = now.replace(hour=14)
        ticks = 0
        seen = set()

        while now < end:
            if now.hour < 10:
                # Not yet promised for today.
                status, stops, expected = STATUS_SHIPPED, None, date(2026, 7, 31)
            elif now < out_for_delivery_at:
                status, stops, expected = STATUS_SHIPPED, None, TODAY
            else:
                status, expected = STATUS_OUT_FOR_DELIVERY, TODAY
                minutes_out = (now - out_for_delivery_at).total_seconds() / 60
                stops = max(0, 20 - int(minutes_out / 4)) if with_stops else None
                if ever_delivered and with_stops and stops == 0:
                    status = STATUS_DELIVERED
                elif ever_delivered and not with_stops and minutes_out > 100:
                    status = STATUS_DELIVERED

            current = compute_state(
                status=status,
                expected_date=expected,
                window_start=time(14, 0),
                window_end=time(16, 0),
                stops_remaining=stops,
                now=now,
                config=config,
            )
            seen.add(current)
            ticks += 1
            now += timedelta(minutes=next_interval_minutes([current], now, config))

        return ticks, seen

    def test_a_normal_delivery_day_is_cheap(self):
        ticks, seen = self._simulate(with_stops=True, ever_delivered=True)
        self.assertIn(STATE_APPROACHING, seen)
        # With the default rhythm a whole day costs a couple of dozen requests,
        # not the ~67 the plan first budgeted.
        self.assertLess(ticks * 2, 60, f"more requests than expected: {ticks} polls")

    def test_a_tighter_rhythm_still_reaches_the_last_rung(self):
        # IMMINENT is skipped at the default pace, because the van goes from
        # two stops to delivered between polls. Someone who wants that rung
        # can buy it with a shorter interval.
        config = Config(jitter_percent=0, poll_window_minutes=5,
                        poll_approaching_minutes=2, poll_imminent_minutes=1)
        now = datetime(2026, 7, 31, 14, 0)
        seen = set()
        while now < datetime(2026, 7, 31, 16, 0):
            minutes_out = (now - datetime(2026, 7, 31, 14, 0)).total_seconds() / 60
            stops = max(0, 20 - int(minutes_out / 4))
            seen.add(compute_state(
                status=STATUS_OUT_FOR_DELIVERY, expected_date=TODAY,
                window_start=time(14, 0), window_end=time(16, 0),
                stops_remaining=stops, now=now, config=config))
            now += timedelta(minutes=next_interval_minutes([list(seen)[-1]], now, config))
        self.assertIn(STATE_IMMINENT, seen)

    def test_the_reserve_slows_things_down_before_the_day_runs_out(self):
        config = Config(jitter_percent=0)
        now = datetime(2026, 7, 31, 15, 0)
        # Plenty left: the fast interval survives.
        self.assertEqual(affordable_interval(3, now, config, used=10, requests_per_poll=3), 3)
        # Almost spent: fall back so the evening still gets polled.
        self.assertEqual(
            affordable_interval(3, now, config, used=config.daily_request_cap - 2,
                                requests_per_poll=3),
            float(config.poll_idle_minutes),
        )

    def test_a_package_that_never_reports_delivered_stays_within_the_cap(self):
        # The pathological case: stops stick at zero and Amazon never confirms.
        # The ladder then polls every minute until an hour past the window, so
        # this asserts the day still fits the request cap that guards it.
        ticks, _ = self._simulate(with_stops=True, ever_delivered=False)
        self.assertLess(
            ticks * 2, Config().daily_request_cap,
            f"a stuck package would blow the daily cap: {ticks} polls",
        )

    def test_without_stop_counts_the_ladder_tops_out_at_window(self):
        _, seen = self._simulate(with_stops=False, ever_delivered=True)
        self.assertNotIn(STATE_IMMINENT, seen)
        self.assertNotIn(STATE_APPROACHING, seen)

    def test_quiet_hours_are_actually_used(self):
        config = Config(jitter_percent=0)
        night = next_interval_minutes([STATE_WINDOW], datetime(2026, 7, 30, 2), config)
        day = next_interval_minutes([STATE_WINDOW], datetime(2026, 7, 30, 15), config)
        self.assertEqual(night, config.poll_idle_minutes)
        self.assertEqual(day, config.poll_window_minutes)


if __name__ == "__main__":
    unittest.main()
