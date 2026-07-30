"""DHL Shipment Tracking — Unified.

Free, and generous enough for a household: 250 calls a day, at most one every
five seconds. Both limits are enforced here rather than discovered by being
cut off.
"""

from __future__ import annotations

import logging
import time as time_module
from datetime import date, datetime, time

import requests

from ..models import (
    STATUS_DELIVERED,
    STATUS_EXCEPTION,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_SHIPPED,
    STATUS_UNKNOWN,
)
from .base import CarrierError, CarrierUpdate, NotFound, RateLimited

LOGGER = logging.getLogger(__name__)

NAME = "DHL"
API_URL = "https://api-eu.dhl.com/track/shipments"
REQUEST_TIMEOUT = 20

# Documented free-tier limits.
DAILY_LIMIT = 250
MIN_SECONDS_BETWEEN_CALLS = 5.0

# DHL's own coarse status. The finer distinction — already in the van — only
# shows up in the wording, so that is checked separately.
STATUS_BY_CODE = {
    "pre-transit": STATUS_ORDERED,
    "transit": STATUS_SHIPPED,
    "delivered": STATUS_DELIVERED,
    "failure": STATUS_EXCEPTION,
    "unknown": STATUS_UNKNOWN,
}

OUT_FOR_DELIVERY_MARKERS = (
    "zustellfahrzeug",
    "in zustellung",
    "out for delivery",
    "wird heute zugestellt",
    "loaded onto the delivery vehicle",
)

# Which carrier names this module answers for.
HANDLES = {"dhl", "deutsche post", "dhl paket"}


def handles(carrier: str | None) -> bool:
    return bool(carrier) and carrier.strip().lower() in HANDLES


def _as_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_date(value: object) -> date | None:
    moment = _as_datetime(value)
    return moment.date() if moment else None


def _as_time(value: object) -> time | None:
    moment = _as_datetime(value)
    return moment.time().replace(second=0, microsecond=0) if moment else None


def parse_shipment(payload: dict) -> CarrierUpdate:
    """Turn DHL's response into our shape. Pure, so it is testable offline."""
    shipments = payload.get("shipments") or []
    if not shipments:
        raise NotFound("no shipment in the response")

    shipment = shipments[0] or {}
    status_block = shipment.get("status") or {}

    code = str(status_block.get("statusCode") or "").lower()
    status = STATUS_BY_CODE.get(code, STATUS_UNKNOWN)

    description = str(status_block.get("description") or status_block.get("status") or "")
    if status == STATUS_SHIPPED and any(
        marker in description.lower() for marker in OUT_FOR_DELIVERY_MARKERS
    ):
        status = STATUS_OUT_FOR_DELIVERY

    frame = shipment.get("estimatedDeliveryTimeFrame") or {}
    location = ""
    address = ((status_block.get("location") or {}).get("address") or {})
    if address.get("addressLocality"):
        location = str(address["addressLocality"])

    return CarrierUpdate(
        status=status,
        expected_date=_as_date(shipment.get("estimatedTimeOfDelivery")),
        window_start=_as_time(frame.get("estimatedFrom")),
        window_end=_as_time(frame.get("estimatedThrough")),
        location=location,
        description=description,
        carrier=NAME,
        source="dhl",
    )


class DhlTracker:
    """Asks DHL, and keeps to their rate limits."""

    name = NAME

    def __init__(self, api_key: str, store=None) -> None:
        self._api_key = api_key
        self._store = store
        self._last_call = 0.0

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _budget_left(self, today: date | None = None) -> int:
        if self._store is None:
            return DAILY_LIMIT
        return DAILY_LIMIT - self._store.carrier_requests_today("dhl", today)

    def _wait_for_slot(self) -> None:
        elapsed = time_module.monotonic() - self._last_call
        if self._last_call and elapsed < MIN_SECONDS_BETWEEN_CALLS:
            pause = MIN_SECONDS_BETWEEN_CALLS - elapsed
            LOGGER.debug("Holding %.1fs to stay inside DHL's rate limit", pause)
            time_module.sleep(pause)

    def fetch(self, tracking_code: str, postal_code: str = "") -> CarrierUpdate:
        if not self.available:
            raise CarrierError("no DHL API key configured")
        if self._budget_left() <= 0:
            raise RateLimited(f"DHL daily limit of {DAILY_LIMIT} reached")

        self._wait_for_slot()

        params = {"trackingNumber": tracking_code, "language": "de"}
        if postal_code:
            # DHL only returns the delivery window when the recipient's postal
            # code proves the asker is the recipient.
            params["recipientPostalCode"] = postal_code

        try:
            response = requests.get(
                API_URL,
                params=params,
                headers={"DHL-API-Key": self._api_key},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as err:
            raise CarrierError(f"DHL request failed: {err}") from err
        finally:
            self._last_call = time_module.monotonic()
            if self._store is not None:
                self._store.count_carrier_request("dhl")

        if response.status_code == 404:
            raise NotFound(f"DHL does not know {tracking_code}")
        if response.status_code == 429:
            raise RateLimited("DHL replied 429; backing off")
        if response.status_code == 401:
            raise CarrierError("DHL rejected the API key")
        if not response.ok:
            raise CarrierError(f"DHL replied {response.status_code}")

        try:
            payload = response.json()
        except ValueError as err:
            raise CarrierError(f"DHL sent no JSON: {err}") from err

        return parse_shipment(payload)
