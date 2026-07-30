"""What a carrier module has to deliver.

The split matters: a *source* (Amazon, later a mailbox) says what was ordered
and hands over a tracking number. A *carrier* says where the parcel is. An
Amazon order shipped by DHL is best answered by DHL — better data, and no
Amazon request spent on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time


@dataclass
class CarrierUpdate:
    """One carrier's answer about one parcel."""

    status: str
    expected_date: date | None = None
    window_start: time | None = None
    window_end: time | None = None
    location: str = ""
    description: str = ""
    carrier: str = ""
    source: str = ""


class CarrierError(Exception):
    """The carrier could not be asked. Callers keep their previous state."""


class NotFound(CarrierError):
    """The carrier does not know this tracking number (yet)."""


class RateLimited(CarrierError):
    """Out of budget for today, or asked too quickly."""
