"""UPS Track.

A client id and secret are exchanged for a bearer token, then one call per
tracking number. UPS states the stage as a single-letter type on the package,
with a human sentence beside it.
"""

from __future__ import annotations

import logging
import uuid
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
from .base import CarrierError, CarrierUpdate, NotFound
from .oauth import REQUEST_TIMEOUT, TokenCarrier

LOGGER = logging.getLogger(__name__)

NAME = "UPS"
TOKEN_URL = "https://onlinetools.ups.com/security/v1/oauth/token"
TRACK_URL = "https://onlinetools.ups.com/api/track/v1/details/{code}"

HANDLES = {"ups"}


# UPS's single-letter package status.
STATUS_BY_TYPE = {
    "M": STATUS_ORDERED,   # label created, not yet with UPS
    "P": STATUS_SHIPPED,   # picked up
    "I": STATUS_SHIPPED,   # in transit
    "O": STATUS_OUT_FOR_DELIVERY,
    "D": STATUS_DELIVERED,
    "X": STATUS_EXCEPTION,
    "RS": STATUS_EXCEPTION,  # returning to sender
}

OUT_FOR_DELIVERY_MARKERS = ("out for delivery", "in zustellung", "loaded on delivery vehicle")


def handles(carrier: str | None) -> bool:
    return bool(carrier) and carrier.strip().lower() in HANDLES


def _as_date(value: object) -> date | None:
    """UPS writes dates as YYYYMMDD."""
    text = str(value or "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _as_time(value: object) -> time | None:
    """UPS writes times as HHMMSS."""
    text = str(value or "")
    if len(text) not in (4, 6) or not text.isdigit():
        return None
    try:
        return datetime.strptime(text.ljust(6, "0"), "%H%M%S").time().replace(second=0)
    except ValueError:
        return None


def parse_shipment(payload: dict) -> CarrierUpdate:
    """Turn UPS's response into our shape. Pure, so it is testable offline."""
    shipments = ((payload.get("trackResponse") or {}).get("shipment")) or []
    if not shipments:
        raise NotFound("no shipment in the response")

    packages = (shipments[0] or {}).get("package") or []
    if not packages:
        raise NotFound("no package in the shipment")

    package = packages[0] or {}
    current = package.get("currentStatus") or {}

    status = STATUS_BY_TYPE.get(str(current.get("type") or "").upper(), STATUS_UNKNOWN)
    description = str(current.get("description") or "")
    if status == STATUS_SHIPPED and any(
        marker in description.lower() for marker in OUT_FOR_DELIVERY_MARKERS
    ):
        status = STATUS_OUT_FOR_DELIVERY

    # deliveryDate holds both the promise and, afterwards, the actual date;
    # the last entry is the current answer either way.
    dates = package.get("deliveryDate") or []
    expected = _as_date(dates[-1].get("date")) if dates else None

    window = package.get("deliveryTime") or {}
    location = ""
    address = ((current.get("location") or {}).get("address") or {})
    if address.get("city"):
        location = str(address["city"])

    return CarrierUpdate(
        status=status,
        expected_date=expected,
        window_start=_as_time(window.get("startTime")),
        window_end=_as_time(window.get("endTime")),
        location=location,
        description=description,
        carrier=NAME,
        source="ups",
    )


class UpsTracker(TokenCarrier):
    name = NAME

    def _token_request(self) -> requests.Response:
        return requests.post(
            TOKEN_URL,
            auth=(self._client_id, self._client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )

    def fetch(self, tracking_code: str, postal_code: str = "") -> CarrierUpdate:
        token = self.token()
        try:
            response = requests.get(
                TRACK_URL.format(code=tracking_code),
                params={"locale": "de_DE", "returnSignature": "false"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "transId": uuid.uuid4().hex,
                    "transactionSrc": "paketbote",
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as err:
            raise CarrierError(f"UPS request failed: {err}") from err
        finally:
            self._count()

        if response.status_code == 404:
            raise NotFound(f"UPS does not know {tracking_code}")
        if response.status_code in (401, 403):
            # The token may simply have aged out; drop it so the next call
            # fetches a fresh one.
            self._token = ""
            raise CarrierError(f"UPS refused the request (HTTP {response.status_code})")
        if not response.ok:
            raise CarrierError(f"UPS replied {response.status_code}")

        try:
            payload = response.json()
        except ValueError as err:
            raise CarrierError(f"UPS sent no JSON: {err}") from err

        return parse_shipment(payload)
