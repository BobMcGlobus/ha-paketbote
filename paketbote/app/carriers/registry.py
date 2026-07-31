"""The carriers a shipment can be filed under, and where to look them up.

DHL, UPS and FedEx are queried automatically once their credentials are in
place. The others are here because a manually added parcel still deserves a
working link, and because this is the list the interface offers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CarrierInfo:
    key: str
    name: str
    tracking_url: str
    automatic: bool = False

    def url_for(self, code: str) -> str:
        return self.tracking_url.format(code=code)


CARRIERS: tuple[CarrierInfo, ...] = (
    CarrierInfo(
        "dhl",
        "DHL",
        "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode={code}",
        automatic=True,
    ),
    CarrierInfo("dpd", "DPD", "https://tracking.dpd.de/status/de_DE/parcel/{code}"),
    CarrierInfo(
        "hermes",
        "Hermes",
        "https://www.myhermes.de/empfangen/sendungsverfolgung/sendungsinformation/#{code}",
    ),
    CarrierInfo("gls", "GLS", "https://gls-group.com/DE/de/paketverfolgung?match={code}"),
    CarrierInfo("ups", "UPS", "https://www.ups.com/track?tracknum={code}", automatic=True),
    CarrierInfo(
        "fedex",
        "FedEx",
        "https://www.fedex.com/fedextrack/?trknbr={code}",
        automatic=True,
    ),
    CarrierInfo("dpost", "Deutsche Post", "https://www.deutschepost.de/sendung/simpleQuery.html?form.sendungsnummer={code}"),
    CarrierInfo("amzl", "AMZL", ""),
    CarrierInfo("other", "Anderer", ""),
)

BY_KEY = {carrier.key: carrier for carrier in CARRIERS}
BY_NAME = {carrier.name.lower(): carrier for carrier in CARRIERS}


def lookup(value: str | None) -> CarrierInfo | None:
    """Find a carrier by key or by display name, however it was written."""
    if not value:
        return None
    needle = value.strip().lower()
    return BY_KEY.get(needle) or BY_NAME.get(needle)


def tracking_url(carrier: str | None, code: str) -> str:
    info = lookup(carrier)
    if info is None or not info.tracking_url or not code:
        return ""
    return info.url_for(code)


def choices() -> list[dict]:
    """What the interface offers in its carrier picker."""
    return [
        {"key": c.key, "name": c.name, "automatic": c.automatic}
        for c in CARRIERS
        if c.key != "amzl"
    ]
