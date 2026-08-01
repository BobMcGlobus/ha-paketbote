"""DHL, read from dhl.de's own tracking endpoint — no key needed.

This is what the tracking page on dhl.de asks for itself. It exists so DHL
works before an API key does, and so a key that gets rejected does not leave
the parcel unknown.

The progress is stated as a position, `fortschritt` out of `maximalFortschritt`,
not as a word. That makes it language-independent, the same property that makes
Amazon's milestones reliable.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from ..models import (
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    STATUS_UNKNOWN,
)
from ..parsing import parse_window
from .base import CarrierError, CarrierUpdate, NotFound, RateLimited
from .dhl import OUT_FOR_DELIVERY_MARKERS
from .scraping import WebTracker

LOGGER = logging.getLogger(__name__)

NAME = "DHL"
API_URL = "https://www.dhl.de/int-verfolgen/data/search"

# DHL's five stages. The last one is delivery, so it is read from the maximum
# rather than assumed to be 5.
STAGE_ANNOUNCED = 1
STAGE_OUT_FOR_DELIVERY = 4

# Fields that have carried a delivery date or window in the responses seen so
# far. The endpoint is undocumented, so this list is a best effort: anything
# not found simply stays None, and Amazon's own figure keeps applying.
DATE_KEYS = ("zustellDatum", "zustelldatum", "datum", "prognostiziertesZustelldatum")
WINDOW_KEYS = ("zustellzeitfenster", "zeitfenster", "avisierungstext", "hinweis", "text")


def _shipment(payload: dict) -> dict:
    shipments = payload.get("sendungen") or []
    if not shipments:
        raise NotFound("no shipment in the response")
    return shipments[0] or {}


def _as_date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")[:19]).date()
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value[:10], pattern).date()
        except ValueError:
            continue
    return None


def _collect(node: object, keys: tuple[str, ...], found: list, depth: int = 0) -> list:
    """Values stored under any of `keys`, wherever they sit in the tree.

    The nesting of this response is not documented and has no reason to stay
    put, so the field is looked for by name rather than by path.
    """
    if depth > 6 or len(found) > 20:
        return found
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and isinstance(value, str) and value:
                found.append(value)
            else:
                _collect(value, keys, found, depth + 1)
    elif isinstance(node, list):
        for item in node[:10]:
            _collect(item, keys, found, depth + 1)
    return found


def parse_shipment(payload: dict) -> CarrierUpdate:
    """Turn dhl.de's response into our shape. Pure, so it is testable offline."""
    if payload.get("rateLimited"):
        raise RateLimited("dhl.de is rate limiting us")

    shipment = _shipment(payload)

    # DHL answers 200 with an explicit "nothing known" rather than a 404.
    if (shipment.get("sendungNichtGefunden") or {}).get("keineDatenVerfuegbar"):
        raise NotFound("dhl.de knows nothing about this number yet")

    details = shipment.get("sendungsdetails") or {}
    progress = details.get("sendungsverlauf") or {}

    step = progress.get("fortschritt")
    last_step = progress.get("maximalFortschritt")
    step = step if isinstance(step, int) else 0
    last_step = last_step if isinstance(last_step, int) and last_step > 0 else 5

    if details.get("istZugestellt") or step >= last_step:
        status = STATUS_DELIVERED
    elif step >= STAGE_OUT_FOR_DELIVERY:
        status = STATUS_OUT_FOR_DELIVERY
    elif step > STAGE_ANNOUNCED:
        status = STATUS_SHIPPED
    elif step == STAGE_ANNOUNCED:
        status = STATUS_ORDERED
    else:
        status = STATUS_UNKNOWN

    # The newest event is the last one, and carries the wording and the place.
    events = progress.get("events") or []
    latest = events[-1] if events and isinstance(events[-1], dict) else {}
    description = str(latest.get("status") or latest.get("text") or "")
    location = str(latest.get("ort") or "")

    # The stage number does not distinguish "at the depot" from "in the van";
    # the wording does, exactly as with the documented API.
    if status == STATUS_SHIPPED and any(
        marker in description.lower() for marker in OUT_FOR_DELIVERY_MARKERS
    ):
        status = STATUS_OUT_FOR_DELIVERY

    expected = None
    for value in _collect(details, DATE_KEYS, []):
        expected = _as_date(value)
        if expected:
            break

    window_start = window_end = None
    for text in _collect(details, WINDOW_KEYS, []):
        window = parse_window(text)
        if window:
            window_start, window_end = window
            break

    return CarrierUpdate(
        status=status,
        expected_date=expected,
        window_start=window_start,
        window_end=window_end,
        location=location,
        description=description,
        carrier=NAME,
        source="dhl_web",
    )


class DhlWebTracker(WebTracker):
    """Reads dhl.de directly, for when there is no working API key."""

    name = NAME
    wants_postcode = True

    def fetch(self, tracking_code: str, postal_code: str = "") -> CarrierUpdate:
        params = {"piececode": tracking_code, "inputLanguage": "de"}
        if postal_code:
            # As with the documented API, the window is only shown to someone
            # who can name the recipient's postal code.
            params["zip"] = postal_code

        response = self._get(API_URL, params, {"Referer": "https://www.dhl.de/"})

        if response.status_code == 429:
            raise RateLimited("dhl.de replied 429; backing off")
        if not response.ok:
            raise CarrierError(f"dhl.de replied {response.status_code}")

        try:
            payload = response.json()
        except ValueError as err:
            raise CarrierError(f"dhl.de sent no JSON: {err}") from err

        return parse_shipment(payload)

    def probe(self) -> tuple[bool, str]:
        try:
            response = self._get(
                API_URL,
                {"piececode": "00340434000000000000", "inputLanguage": "de"},
                {"Referer": "https://www.dhl.de/"},
            )
        except CarrierError as err:
            return False, str(err)
        if response.ok:
            return True, "dhl.de answers (no key needed)"
        return False, f"dhl.de replied {response.status_code}"
