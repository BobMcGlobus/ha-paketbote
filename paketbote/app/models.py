"""Shared data shapes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time

TITLE_MAX_LENGTH = 60

# Polling states, in escalating order of urgency.
STATE_IDLE = "IDLE"
STATE_PENDING = "PENDING"
STATE_WINDOW = "WINDOW"
STATE_APPROACHING = "APPROACHING"
STATE_IMMINENT = "IMMINENT"
STATE_DELIVERED = "DELIVERED"

STATUS_UNKNOWN = "unknown"


def shorten(title: str, limit: int = TITLE_MAX_LENGTH) -> str:
    """Collapse whitespace and cut an item name down to something displayable."""
    collapsed = re.sub(r"\s+", " ", title or "").strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


@dataclass
class Shipment:
    """One trackable package.

    Phase 2 fills in only what the order overview reveals — identifiers, a
    title and the tracking URL. Everything below `carrier` stays at its default
    until the extractor lands in phase 3.
    """

    shipment_id: str
    order_id: str
    tracking_url: str
    title: str = ""
    carrier: str | None = None
    status: str = STATUS_UNKNOWN
    stops_remaining: int | None = None
    window_start: time | None = None
    window_end: time | None = None
    expected_date: date | None = None
    state: str = STATE_IDLE
    last_seen: datetime | None = None


@dataclass
class TrackingPage:
    """A raw capture of one progress-tracker page.

    Phase 2 stops here on purpose: no parsing, no interpretation. The text is
    what phase 3 will hand to the LLM, and what the repo keeps as fixtures.
    """

    shipment: Shipment
    url: str
    page_title: str
    text: str
    fetched_at: datetime = field(default_factory=datetime.now)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()
