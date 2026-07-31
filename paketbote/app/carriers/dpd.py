"""DPD, read from their own tracking page.

DPD has no consumer API, and the tracking link goes through a data-protection
page that asks for the recipient's postal code. That page turns out to be a
redirect, not a wall: `redirect.aspx?action=12&parcelno=...` lands on the
tracking page for the basic status either way, and the postal code only
matters for the detailed view.

The useful part is that DPD states the stage twice, and neither depends on the
language: the progress icon is `status_N.svg`, and each of the five stages
carries a date once it has been reached.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime

from ..models import (
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    STATUS_UNKNOWN,
)
from ..parsing import parse_window
from .base import CarrierError, CarrierUpdate, NotFound
from .scraping import WebTracker

LOGGER = logging.getLogger(__name__)

NAME = "DPD"
TRACK_URL = "https://my.dpd.de/redirect.aspx"

HANDLES = {"dpd", "dpd deutschland"}

# DPD's five stages, in the order their own progress list shows them.
STAGES = ("Start", "OnTheRoad", "DeliveryDepot", "CarLoad", "Delivered")

STATUS_BY_STAGE = {
    1: STATUS_ORDERED,            # Auftragsdaten an DPD übermittelt
    2: STATUS_SHIPPED,            # Paket unterwegs
    3: STATUS_SHIPPED,            # Paket im Paketzustellzentrum
    4: STATUS_OUT_FOR_DELIVERY,   # Paket in Zustellung
    5: STATUS_DELIVERED,          # Paket zugestellt
}

_STAGE_IMAGE_RE = re.compile(r'imgParcelStatus"[^>]*src="[^"]*status_(\d)\.svg', re.I)
_STATUS_TEXT_RE = re.compile(r'labDeliveryStatus_0"[^>]*>(.*?)</span>', re.S)
_STAGE_DATE_RE = re.compile(r'labStatus(\w+?)Date"[^>]*>([^<]*)</span>')
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def handles(carrier: str | None) -> bool:
    return bool(carrier) and carrier.strip().lower() in HANDLES


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _as_date(stamp: str, today: date) -> date | None:
    """DPD writes `31.07.` — the day and month, never the year."""
    match = re.match(r"\s*(\d{1,2})\.(\d{1,2})\.?\s*$", stamp or "")
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    try:
        found = date(today.year, month, day)
    except ValueError:
        return None
    # A date more than a month ahead is last year's, seen across New Year.
    if (found - today).days > 31:
        try:
            found = date(today.year - 1, month, day)
        except ValueError:
            return None
    return found


def parse_page(body: str, today: date | None = None) -> CarrierUpdate:
    """Turn DPD's tracking page into our shape. Pure, so it is testable offline."""
    today = today or date.today()

    stage_match = _STAGE_IMAGE_RE.search(body)
    dates = {name: value.strip() for name, value in _STAGE_DATE_RE.findall(body)}
    reached = [STAGES.index(name) + 1 for name in STAGES if dates.get(name)]

    if stage_match:
        stage = int(stage_match.group(1))
    elif reached:
        # The icon is the primary signal; the dated stages are the fallback.
        stage = max(reached)
    else:
        raise NotFound("no parcel status on the page")

    status = STATUS_BY_STAGE.get(stage, STATUS_UNKNOWN)

    text_match = _STATUS_TEXT_RE.search(body)
    description = _text(text_match.group(1)) if text_match else ""

    # A stage date is the date of that event, not a promise. Only once the
    # parcel is in the van, or delivered, does it mean the day of delivery —
    # anything earlier would put an arrival date in the past.
    expected = None
    if stage >= 4:
        for name in reversed(STAGES):
            if dates.get(name):
                expected = _as_date(dates[name], today)
                if expected:
                    break

    # DPD names an hour slot on the day of delivery; if it is on the page,
    # the pattern that already reads Amazon's and DHL's windows will find it.
    window = parse_window(_text(_SCRIPT_RE.sub(" ", body)))

    return CarrierUpdate(
        status=status,
        expected_date=expected,
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
        location="",
        description=description,
        carrier=NAME,
        source="dpd",
    )


class DpdTracker(WebTracker):
    name = NAME

    def fetch(self, tracking_code: str, postal_code: str = "") -> CarrierUpdate:
        params = {"action": "12", "parcelno": tracking_code}
        if postal_code:
            # Gets past the data-protection page in one step rather than two.
            params["zip"] = postal_code

        response = self._get(TRACK_URL, params)

        if response.status_code == 404:
            raise NotFound(f"DPD does not know {tracking_code}")
        if not response.ok:
            raise CarrierError(f"DPD replied {response.status_code}")

        return parse_page(response.text)

    def probe(self) -> tuple[bool, str]:
        try:
            response = self._get(TRACK_URL, {"action": "12", "parcelno": "00000000000000"})
        except CarrierError as err:
            return False, str(err)
        if response.ok:
            return True, "DPD answers (no key needed)"
        return False, f"DPD replied {response.status_code}"
