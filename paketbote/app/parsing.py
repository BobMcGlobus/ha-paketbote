"""Language-aware parsing of Amazon's delivery wording.

Kept free of Playwright and of any I/O so it can be tested against fixtures.
Both German and English are handled: the account language decides what Amazon
renders, and it is not necessarily the domain's language.
"""

from __future__ import annotations

import re
from datetime import date, time

from .models import (
    STATUS_DELIVERED,
    STATUS_EXCEPTION,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
)

MONTHS: dict[str, int] = {}
for _index, _names in enumerate(
    [
        ("january", "jan", "januar"),
        ("february", "feb", "februar"),
        ("march", "mar", "märz", "maerz", "mrz"),
        ("april", "apr"),
        ("may", "mai"),
        ("june", "jun", "juni"),
        ("july", "jul", "juli"),
        ("august", "aug"),
        ("september", "sep", "sept"),
        ("october", "oct", "okt", "oktober"),
        ("november", "nov"),
        ("december", "dec", "dez", "dezember"),
    ],
    start=1,
):
    for _name in _names:
        MONTHS[_name] = _index

_MONTH_ALTERNATION = "|".join(sorted(MONTHS, key=len, reverse=True))

# "12 August", "1. August", "28 Juli"
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})\.?\s+({_MONTH_ALTERNATION})\b", re.IGNORECASE)
# "Jul 31", "August 12"
_MONTH_DAY_RE = re.compile(rf"\b({_MONTH_ALTERNATION})\.?\s+(\d{{1,2}})\b", re.IGNORECASE)

TODAY_MARKERS = ("today", "heute")
TOMORROW_MARKERS = ("tomorrow", "morgen")

# "zwischen 14:00 und 18:00", "14:00 - 18:00", "2:00 PM to 6:00 PM"
_WINDOW_HHMM_RE = re.compile(
    r"(\d{1,2})[:.](\d{2})\s*(?:uhr\s*)?(?:und|and|bis|to|[-–—])\s*(\d{1,2})[:.](\d{2})",
    re.IGNORECASE,
)
# "between 2pm and 6pm", "2 PM - 6 PM"
_WINDOW_AMPM_RE = re.compile(
    r"(\d{1,2})\s*(am|pm)\s*(?:and|to|[-–—])\s*(\d{1,2})\s*(am|pm)",
    re.IGNORECASE,
)
# "zwischen 14 und 18 Uhr"
_WINDOW_HOURS_RE = re.compile(
    r"zwischen\s*(\d{1,2})\s*(?:und|bis|[-–—])\s*(\d{1,2})\s*uhr",
    re.IGNORECASE,
)

# Only ever a real stop count: the plan says guess nothing here.
_STOPS_RES = (
    re.compile(r"(\d{1,3})\s*(?:more\s+)?stops?\s*(?:away|remaining|left|to go)", re.IGNORECASE),
    re.compile(r"noch\s*(\d{1,3})\s*(?:stopps?|haltestellen?)", re.IGNORECASE),
    re.compile(r"(\d{1,3})\s*(?:stopps?|haltestellen?)\s*(?:entfernt|verbleibend)", re.IGNORECASE),
)

# Milestone order on the progress tracker. The bar is read by position, not by
# label, so the account language does not matter.
STATUS_BY_MILESTONE = (
    STATUS_ORDERED,
    STATUS_SHIPPED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_DELIVERED,
)

# Used only when the bar has an unexpected number of steps.
STATUS_BY_LABEL = {
    "ordered": STATUS_ORDERED,
    "bestellt": STATUS_ORDERED,
    "dispatched": STATUS_SHIPPED,
    "shipped": STATUS_SHIPPED,
    "versandt": STATUS_SHIPPED,
    "verschickt": STATUS_SHIPPED,
    "out for delivery": STATUS_OUT_FOR_DELIVERY,
    "in zustellung": STATUS_OUT_FOR_DELIVERY,
    "wird zugestellt": STATUS_OUT_FOR_DELIVERY,
    "delivered": STATUS_DELIVERED,
    "zugestellt": STATUS_DELIVERED,
    "geliefert": STATUS_DELIVERED,
    "delayed": STATUS_EXCEPTION,
    "verspätet": STATUS_EXCEPTION,
}

CARRIERS = {
    "amazon": "AMZL",
    "amzl": "AMZL",
    "dhl": "DHL",
    "hermes": "Hermes",
    "dpd": "DPD",
    "gls": "GLS",
    "ups": "UPS",
    "fedex": "FedEx",
    "deutsche post": "Deutsche Post",
}


def _pick_year(month: int, day: int, today: date) -> int:
    """Choose the year that puts this day/month closest to today."""
    best_year, best_gap = today.year, None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        gap = abs((candidate - today).days)
        if best_gap is None or gap < best_gap:
            best_year, best_gap = year, gap
    return best_year


def parse_expected_date(text: str, today: date) -> date | None:
    """Read a delivery date out of Amazon's promise wording.

    Handles "Arriving tomorrow", "Kommt heute", "Arriving 12 August - 13 August"
    and "Get it Tomorrow, Jul 31". With a range, the earliest date wins, since
    that is the first day worth watching for.
    """
    if not text:
        return None
    lowered = text.lower()

    if any(marker in lowered for marker in TOMORROW_MARKERS):
        return date.fromordinal(today.toordinal() + 1)
    if any(marker in lowered for marker in TODAY_MARKERS):
        return today

    candidates: list[date] = []
    for match in _DAY_MONTH_RE.finditer(lowered):
        day, month = int(match.group(1)), MONTHS[match.group(2)]
        candidates.append(_safe_date(_pick_year(month, day, today), month, day))
    for match in _MONTH_DAY_RE.finditer(lowered):
        month, day = MONTHS[match.group(1)], int(match.group(2))
        candidates.append(_safe_date(_pick_year(month, day, today), month, day))

    valid = [d for d in candidates if d is not None]
    return min(valid) if valid else None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_window(text: str) -> tuple[time, time] | None:
    """Read a delivery time window, or None when there is none."""
    if not text:
        return None

    match = _WINDOW_HHMM_RE.search(text)
    if match:
        return _times(int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)))

    match = _WINDOW_AMPM_RE.search(text)
    if match:
        start = _from_ampm(int(match.group(1)), match.group(2))
        end = _from_ampm(int(match.group(3)), match.group(4))
        return _times(start, 0, end, 0)

    match = _WINDOW_HOURS_RE.search(text)
    if match:
        return _times(int(match.group(1)), 0, int(match.group(2)), 0)

    return None


def _from_ampm(hour: int, marker: str) -> int:
    hour %= 12
    return hour + 12 if marker.lower() == "pm" else hour


def _times(h1: int, m1: int, h2: int, m2: int) -> tuple[time, time] | None:
    try:
        return time(h1, m1), time(h2, m2)
    except ValueError:
        return None


def parse_stops(text: str) -> int | None:
    """Read "X stops away". Returns None unless a stop count is spelled out."""
    if not text:
        return None
    for pattern in _STOPS_RES:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def status_from_label(label: str) -> str | None:
    """Map a milestone label to a status. Fallback for unexpected bar shapes."""
    lowered = (label or "").strip().lower()
    for needle, status in STATUS_BY_LABEL.items():
        if needle in lowered:
            return status
    return None


def detect_carrier(text: str) -> str | None:
    """Name the carrier if the page says who is delivering."""
    lowered = (text or "").lower()
    for needle, name in CARRIERS.items():
        if needle in lowered:
            return name
    return None
