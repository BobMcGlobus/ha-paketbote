"""Hermes, read from the endpoint their own tracking page uses.

Hermes has no consumer API and no key to apply for, but the page at
myhermes.de fetches plain JSON from `api.my-deliveries.de` with no
authentication at all. That is what this asks.

The useful part is `forecast`: Hermes names a delivery window in UTC, which is
more than Amazon ever says about a Hermes parcel.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone

from ..models import (
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    STATUS_UNKNOWN,
)
from .base import CarrierError, CarrierUpdate, NotFound
from .scraping import WebTracker

LOGGER = logging.getLogger(__name__)

NAME = "Hermes"
API_URL = "https://api.my-deliveries.de/tnt/v2/shipments/search/{code}"

HANDLES = {"hermes", "myhermes", "hermes germany"}

OUT_FOR_DELIVERY_MARKERS = (
    "zustellfahrzeug",
    "in zustellung",
    "wird heute zugestellt",
    "out for delivery",
    "zustellung erfolgt",
)

DELIVERED_MARKERS = ("zugestellt", "delivered", "abgeholt worden")


def handles(carrier: str | None) -> bool:
    return bool(carrier) and carrier.strip().lower() in HANDLES


def _as_local(value: object) -> datetime | None:
    """Hermes states the forecast in UTC; the household thinks in local time."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


def _as_date(value: object) -> date | None:
    moment = _as_local(value)
    return moment.date() if moment else None


def _as_time(value: object) -> time | None:
    moment = _as_local(value)
    return moment.time().replace(second=0, microsecond=0) if moment else None


def parse_shipment(payload: object) -> CarrierUpdate:
    """Turn the Hermes response into our shape. Pure, so it is testable offline."""
    # The search endpoint answers with a list, even for one number.
    if isinstance(payload, dict):
        shipments = payload.get("shipments") or [payload]
    elif isinstance(payload, list):
        shipments = payload
    else:
        raise NotFound("unrecognised response")

    if not shipments or not isinstance(shipments[0], dict):
        raise NotFound("no shipment in the response")

    shipment = shipments[0]
    details = shipment.get("shipmentDetails") or {}
    attributes = details.get("parcelAttributes") or {}

    history = details.get("history") or shipment.get("history") or []
    latest = history[-1] if history and isinstance(history[-1], dict) else {}
    description = str(
        latest.get("historyText") or shipment.get("statusText") or ""
    )
    lowered = description.lower()

    if attributes.get("delivered") or any(m in lowered for m in DELIVERED_MARKERS):
        status = STATUS_DELIVERED
    elif any(marker in lowered for marker in OUT_FOR_DELIVERY_MARKERS):
        status = STATUS_OUT_FOR_DELIVERY
    elif history:
        status = STATUS_SHIPPED
    elif shipment.get("barcode") or details:
        status = STATUS_ORDERED
    else:
        status = STATUS_UNKNOWN

    forecast = details.get("forecast") or {}
    starts = forecast.get("deliveryTimeFromUTC")
    ends = forecast.get("deliveryTimeToUTC")

    expected = _as_date(starts)
    if expected is None:
        expected = _as_date(latest.get("timestamp")) if status == STATUS_DELIVERED else None

    location = str((details.get("address") or {}).get("city") or "")

    return CarrierUpdate(
        status=status,
        expected_date=expected,
        window_start=_as_time(starts),
        window_end=_as_time(ends),
        location=location,
        description=description,
        carrier=NAME,
        source="hermes",
    )


class HermesTracker(WebTracker):
    name = NAME

    def fetch(self, tracking_code: str, postal_code: str = "") -> CarrierUpdate:
        response = self._get(
            API_URL.format(code=tracking_code),
            headers={"X-Language": "de", "Origin": "https://www.myhermes.de"},
        )

        if response.status_code in (400, 404):
            # 400 is a malformed number, 404 a well-formed one Hermes does not
            # know yet. Neither is worth an error in the log.
            raise NotFound(f"Hermes does not know {tracking_code}")
        if not response.ok:
            raise CarrierError(f"Hermes replied {response.status_code}")

        try:
            payload = response.json()
        except ValueError as err:
            raise CarrierError(f"Hermes sent no JSON: {err}") from err

        return parse_shipment(payload)

    def probe(self) -> tuple[bool, str]:
        try:
            response = self._get(API_URL.format(code="00000000000000"))
        except CarrierError as err:
            return False, str(err)
        # Being told the number is nonsense proves the endpoint is reachable.
        if response.status_code in (200, 400, 404):
            return True, f"Hermes answers (HTTP {response.status_code}, no key needed)"
        return False, f"Hermes replied {response.status_code}"
