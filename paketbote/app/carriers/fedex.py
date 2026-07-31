"""FedEx Track API.

Same shape as UPS — client credentials for a bearer token — but the tracking
numbers go in a POST body, and the status arrives as a two-letter derived code
that FedEx normalises across their own operating companies.
"""

from __future__ import annotations

import logging
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
from .oauth import REQUEST_TIMEOUT, TokenCarrier

LOGGER = logging.getLogger(__name__)

NAME = "FedEx"
TOKEN_URL = "https://apis.fedex.com/oauth/token"
TRACK_URL = "https://apis.fedex.com/track/v1/trackingnumbers"

HANDLES = {"fedex", "fedex express", "tnt"}

# FedEx answers from the tracking number alone.
WANTS_POSTCODE = False

# FedEx's derivedCode: the same handful of stages whichever of their networks
# actually carries the parcel.
STATUS_BY_CODE = {
    "OC": STATUS_ORDERED,        # order created, label only
    "PU": STATUS_SHIPPED,        # picked up
    "DP": STATUS_SHIPPED,        # departed facility
    "AR": STATUS_SHIPPED,        # arrived at facility
    "AF": STATUS_SHIPPED,        # at facility
    "IT": STATUS_SHIPPED,        # in transit
    "IX": STATUS_SHIPPED,        # in transit, international
    "OD": STATUS_OUT_FOR_DELIVERY,
    "DL": STATUS_DELIVERED,
    "DE": STATUS_EXCEPTION,      # delivery exception
    "SE": STATUS_EXCEPTION,      # shipment exception
    "CA": STATUS_EXCEPTION,      # cancelled
    "RS": STATUS_EXCEPTION,      # returned to shipper
}

# Which of the many date entries FedEx returns is the one worth showing,
# most trustworthy first.
DATE_TYPES = ("ACTUAL_DELIVERY", "ESTIMATED_DELIVERY", "COMMITMENT")


def handles(carrier: str | None) -> bool:
    return bool(carrier) and carrier.strip().lower() in HANDLES


def _as_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # Some entries are a bare date.
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError:
            return None


def _as_date(value: object) -> date | None:
    moment = _as_datetime(value)
    return moment.date() if moment else None


def _as_time(value: object) -> time | None:
    moment = _as_datetime(value)
    return moment.time().replace(second=0, microsecond=0) if moment else None


def _first_date(entries: list) -> date | None:
    by_type = {}
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("type"):
            by_type.setdefault(str(entry["type"]).upper(), entry.get("dateTime"))
    for wanted in DATE_TYPES:
        if wanted in by_type:
            found = _as_date(by_type[wanted])
            if found:
                return found
    return None


def parse_shipment(payload: dict) -> CarrierUpdate:
    """Turn FedEx's response into our shape. Pure, so it is testable offline."""
    results = ((payload.get("output") or {}).get("completeTrackResults")) or []
    if not results:
        raise NotFound("no track result in the response")

    tracks = (results[0] or {}).get("trackResults") or []
    if not tracks:
        raise NotFound("no track result in the response")

    track = tracks[0] or {}

    # FedEx reports "we do not know this number" as a per-result error rather
    # than an HTTP status.
    error = track.get("error")
    if error and not track.get("latestStatusDetail"):
        message = str(error.get("message") or error.get("code") or "unknown tracking number")
        raise NotFound(f"FedEx: {message}")

    latest = track.get("latestStatusDetail") or {}
    code = str(latest.get("derivedCode") or latest.get("code") or "").upper()
    status = STATUS_BY_CODE.get(code, STATUS_UNKNOWN)

    description = str(latest.get("statusByLocale") or latest.get("description") or "")

    location = ""
    place = latest.get("scanLocation") or {}
    if place.get("city"):
        location = str(place["city"])

    expected = _first_date(track.get("dateAndTimes") or [])

    # The promised slot, when FedEx commits to one.
    window = (track.get("standardTransitTimeWindow") or {}).get("window") or {}
    estimated = (track.get("estimatedDeliveryTimeWindow") or {}).get("window") or window

    return CarrierUpdate(
        status=status,
        expected_date=expected,
        window_start=_as_time(estimated.get("begins")),
        window_end=_as_time(estimated.get("ends")),
        location=location,
        description=description,
        carrier=NAME,
        source="fedex",
    )


class FedexTracker(TokenCarrier):
    name = NAME

    def _token_request(self) -> requests.Response:
        # FedEx wants the credentials in the body, not as basic auth.
        return requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=REQUEST_TIMEOUT,
        )

    def fetch(self, tracking_code: str, postal_code: str = "") -> CarrierUpdate:
        token = self.token()
        body = {
            "includeDetailedScans": False,
            "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": tracking_code}}],
        }
        try:
            response = requests.post(
                TRACK_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-locale": "de_DE",
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as err:
            raise CarrierError(f"FedEx request failed: {err}") from err
        finally:
            self._count()

        if response.status_code == 404:
            raise NotFound(f"FedEx does not know {tracking_code}")
        if response.status_code == 429:
            raise RateLimited("FedEx replied 429; backing off")
        if response.status_code in (401, 403):
            # Most likely an aged-out token; drop it so the next call renews.
            self._token = ""
            raise CarrierError(f"FedEx refused the request (HTTP {response.status_code})")
        if not response.ok:
            raise CarrierError(f"FedEx replied {response.status_code}")

        try:
            payload = response.json()
        except ValueError as err:
            raise CarrierError(f"FedEx sent no JSON: {err}") from err

        return parse_shipment(payload)
