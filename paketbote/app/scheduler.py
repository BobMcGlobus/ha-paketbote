"""The polling ladder.

Every function here is pure: state is recomputed from the current facts rather
than advanced step by step, which is what makes the backwards transitions the
plan asks for fall out for free. Amazon does revise stop counts upwards.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, time, timedelta

from .config import Config
from .models import (
    STATE_APPROACHING,
    STATE_DELIVERED,
    STATE_IDLE,
    STATE_IMMINENT,
    STATE_PENDING,
    STATE_WINDOW,
    STATUS_DELIVERED,
    STATUS_OUT_FOR_DELIVERY,
    Shipment,
)

LOGGER = logging.getLogger(__name__)

# How long after the promised window closes a shipment stops being polled.
WINDOW_GRACE_MINUTES = 60

# Least to most urgent. Used both for picking the poll interval and for the
# aggregate status sensor.
STATE_ORDER = (
    STATE_DELIVERED,
    STATE_IDLE,
    STATE_PENDING,
    STATE_WINDOW,
    STATE_APPROACHING,
    STATE_IMMINENT,
)

INTERVAL_OPTION = {
    STATE_IDLE: "poll_idle_minutes",
    STATE_PENDING: "poll_pending_minutes",
    STATE_WINDOW: "poll_window_minutes",
    STATE_APPROACHING: "poll_approaching_minutes",
    STATE_IMMINENT: "poll_imminent_minutes",
    STATE_DELIVERED: "poll_idle_minutes",
}

ACTIVE_WINDOW_STATES = (STATE_WINDOW, STATE_APPROACHING, STATE_IMMINENT)


def compute_state(
    *,
    status: str,
    expected_date: date | None,
    window_start: time | None,
    window_end: time | None,
    stops_remaining: int | None,
    now: datetime,
    config: Config,
) -> str:
    """Which rung of the ladder this shipment is on, right now."""
    if status == STATUS_DELIVERED:
        return STATE_DELIVERED

    today = now.date()

    # Promised for a later day, or no date at all and not visibly moving.
    if expected_date is not None and expected_date > today:
        return STATE_IDLE
    if expected_date is None and status != STATUS_OUT_FOR_DELIVERY:
        return STATE_IDLE

    # An hour past the promised window, stop watching. Delivered or not, there
    # is nothing more to learn by polling.
    #
    # amazon.de usually names no window at all, and without an end time this
    # rule would never fire: a package stuck at "1 stop away" would then be
    # polled every minute until quiet hours and eat the whole day's request
    # budget. Treat the start of quiet hours as the end of the delivery day.
    if window_end is not None:
        effective_end = window_end
    elif config.quiet_hours_start != config.quiet_hours_end:
        effective_end = time(config.quiet_hours_start, 0)
    else:
        effective_end = time(23, 59)

    closes = datetime.combine(today, effective_end) + timedelta(minutes=WINDOW_GRACE_MINUTES)
    if now > closes:
        return STATE_DELIVERED

    # Out for delivery counts as an open window even when Amazon names no times,
    # which is the common case on amazon.de.
    window_open = status == STATUS_OUT_FOR_DELIVERY
    if window_start is not None and now.time() >= window_start:
        window_open = True
    if not window_open:
        return STATE_PENDING

    # Only AMZL deliveries carry stop counts; without one the ladder simply
    # stops at WINDOW.
    if stops_remaining is not None:
        if stops_remaining < config.imminent_stops_threshold:
            return STATE_IMMINENT
        if stops_remaining < config.approaching_stops_threshold:
            return STATE_APPROACHING

    return STATE_WINDOW


def state_for(shipment: Shipment, now: datetime, config: Config) -> str:
    return compute_state(
        status=shipment.status,
        expected_date=shipment.expected_date,
        window_start=shipment.window_start,
        window_end=shipment.window_end,
        stops_remaining=shipment.stops_remaining,
        now=now,
        config=config,
    )


def most_urgent(states: list[str]) -> str:
    """The state that decides the aggregate sensor and the poll interval."""
    if not states:
        return STATE_IDLE
    return max(states, key=lambda s: STATE_ORDER.index(s) if s in STATE_ORDER else 0)


def in_quiet_hours(now: datetime, config: Config) -> bool:
    """Amazon does not deliver at three in the morning."""
    start, end = config.quiet_hours_start, config.quiet_hours_end
    if start == end:
        return False
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def apply_jitter(minutes: float, percent: int, rng: random.Random | None = None) -> float:
    """Exactly periodic requests are the most conspicuous pattern there is."""
    if percent <= 0:
        return float(minutes)
    rng = rng or random
    spread = minutes * percent / 100.0
    return max(0.5, rng.uniform(minutes - spread, minutes + spread))


def next_interval_minutes(
    states: list[str],
    now: datetime,
    config: Config,
    rng: random.Random | None = None,
) -> float:
    """Minimum interval over all shipments, damped by night and jitter."""
    if in_quiet_hours(now, config):
        base = config.poll_idle_minutes
        LOGGER.debug("Quiet hours: holding the idle interval")
    elif states:
        base = min(getattr(config, INTERVAL_OPTION[s]) for s in states if s in INTERVAL_OPTION)
    else:
        base = config.poll_idle_minutes
    return apply_jitter(base, config.jitter_percent, rng)


def budget_reserve(now: datetime, config: Config, requests_per_poll: int) -> int:
    """How many requests to hold back for the rest of the day.

    Enough to keep polling at the idle rhythm until quiet hours begin. Without
    this, one busy delivery afternoon spends the whole allowance and the
    add-on goes silent while parcels are still moving.
    """
    end_hour = config.quiet_hours_start if config.quiet_hours_start != config.quiet_hours_end else 24
    hours_left = max(0.0, end_hour - (now.hour + now.minute / 60))
    polls_left = hours_left * 60 / max(1, config.poll_idle_minutes)
    return int(polls_left * max(1, requests_per_poll))


def affordable_interval(
    minutes: float,
    now: datetime,
    config: Config,
    used: int,
    requests_per_poll: int,
) -> float:
    """Slow down to the idle rhythm once the day's remaining budget is needed.

    Returns the interval unchanged while there is surplus.
    """
    remaining = config.daily_request_cap - used
    reserve = budget_reserve(now, config, requests_per_poll)
    if remaining <= reserve:
        LOGGER.info(
            "Holding the idle rhythm: %d requests left, %d reserved for the rest of the day",
            remaining, reserve,
        )
        return float(config.poll_idle_minutes)
    return minutes


def summarise(shipments: list[Shipment], now: datetime, config: Config) -> dict:
    """The aggregate figures, computed here rather than templated in HA.

    The plan is explicit about this: template sensors over a changing list of
    entities break every time a new shipment appears.
    """
    today = now.date()
    live = [s for s in shipments if s.state != STATE_DELIVERED]

    stops = [s.stops_remaining for s in live if s.stops_remaining is not None]
    windows = [s.window_start for s in live if s.window_start is not None]
    states = [s.state for s in live]

    next_window = None
    if windows:
        next_window = datetime.combine(today, min(windows)).astimezone().isoformat()

    return {
        "pakete_heute": sum(1 for s in live if s.expected_date == today),
        "pakete_aktiv": len(live),
        "naechste_stopps": min(stops) if stops else None,
        "gesamtstatus": most_urgent(states),
        "naechstes_fenster": next_window,
        "zustellfenster_aktiv": any(s in ACTIVE_WINDOW_STATES for s in states),
        "zustellung_unmittelbar": any(s == STATE_IMMINENT for s in states),
    }
