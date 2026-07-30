"""Order overview → shipments, progress tracker → raw text.

Nothing here interprets the page beyond identifiers. Turning the text into a
status, a delivery window and a stop count is phase 3's job, and keeping that
split is what lets the extractor be tested against fixtures without Amazon.
"""

from __future__ import annotations

import argparse
import logging
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeout

from .browser import (
    DEFAULT_CDP_URL,
    AttachedBrowser,
    BrowserUnavailable,
    LoginRequired,
)
from .config import LOG_LEVELS, Config
from .models import Shipment, TrackingPage, shorten

LOGGER = logging.getLogger(__name__)

# Link shapes that lead to a tracking page. Matching on URL rather than markup
# is the durable part; this tuple is the maintenance point when Amazon
# reorganises its order pages.
TRACKING_HREF_MARKERS = ("progress-tracker", "ship-track", "shipment-tracking")

# Containers tried in order when reading a tracker page, most specific first.
MAIN_CONTAINER_CANDIDATES = ("#pt-page-container", "main", "#a-page", "body")

RAW_TEXT_LIMIT = 20_000
ORDER_ID_RE = re.compile(r"\b[A-Z0-9]{3}-\d{7}-\d{7}\b")

# Two tracker pages back to back look like a script; the plan asks for a pause.
MIN_PAUSE_SECONDS = 2.0
MAX_PAUSE_SECONDS = 5.0

_COLLECT_TRACKING_LINKS_JS = """
(markers) => {
  const seen = new Set();
  const out = [];
  for (const anchor of document.querySelectorAll('a[href]')) {
    const href = anchor.href;
    if (!markers.some((m) => href.includes(m))) continue;
    if (seen.has(href)) continue;
    seen.add(href);

    // Walk up until we find the card that also holds a product link: that is
    // the order block, whatever Amazon happens to call it this month.
    let node = anchor;
    let title = '';
    for (let depth = 0; depth < 8 && node; depth += 1) {
      const product = node.querySelector('a[href*="/dp/"], a[href*="/gp/product/"]');
      if (product) {
        title = (product.textContent || '').trim();
        break;
      }
      node = node.parentElement;
    }
    out.push({ href, title });
  }
  return out;
}
"""


def sanitise_id(value: str) -> str:
    """Reduce an identifier to something safe for MQTT topics and entity ids."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")


def _query(url: str) -> dict[str, str]:
    """Flatten a URL's query string, lowercasing keys for robust lookups."""
    parsed = urlparse(url)
    flat: dict[str, str] = {}
    for key, values in parse_qs(parsed.query).items():
        if values and values[0]:
            flat[key.lower()] = values[0]
    return flat


def identify(url: str) -> tuple[str, str] | None:
    """Derive (order_id, shipment_id) from a tracking link.

    Returns None when the link carries no order identifier at all, which is how
    generic "show all shipments" links get filtered out.
    """
    params = _query(url)

    order_id = params.get("orderid")
    if not order_id:
        match = ORDER_ID_RE.search(url)
        order_id = match.group(0) if match else ""
    if not order_id:
        return None

    shipment_key = params.get("shipmentid")
    if not shipment_key:
        # One order can ship as several packages; packageIndex is what keeps
        # them apart when Amazon hands out no shipment id.
        shipment_key = f"{order_id}-{params.get('packageindex', '0')}"

    return sanitise_id(order_id), sanitise_id(shipment_key)


def normalise_text(text: str) -> str:
    """Trim trailing spaces and squeeze runs of blank lines."""
    lines = [line.rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_text(page: Page) -> str:
    """Visible text of the most specific container that actually has content."""
    for selector in MAIN_CONTAINER_CANDIDATES:
        try:
            locator = page.locator(selector)
            if locator.count() == 0:
                continue
            text = locator.first.inner_text(timeout=5_000)
        except (PlaywrightError, PlaywrightTimeout):
            LOGGER.debug("Could not read container %s", selector, exc_info=True)
            continue
        if text and text.strip():
            LOGGER.debug("Read %d characters from %s", len(text), selector)
            return normalise_text(text)[:RAW_TEXT_LIMIT]
    return ""


class Scraper:
    """Reads Amazon's pages. Produces raw text, never conclusions."""

    def __init__(self, config: Config, browser: AttachedBrowser) -> None:
        self._config = config
        self._browser = browser

    def list_shipments(self) -> list[Shipment]:
        """Everything the order overview currently offers a tracking link for."""
        with self._browser.visit(self._config.order_history_url) as page:
            found = page.evaluate(_COLLECT_TRACKING_LINKS_JS, list(TRACKING_HREF_MARKERS))

        LOGGER.debug("Order overview yielded %d tracking link(s)", len(found))

        shipments: dict[str, Shipment] = {}
        for entry in found:
            identity = identify(entry["href"])
            if identity is None:
                LOGGER.debug("Skipping link without an order id: %s", entry["href"])
                continue
            order_id, shipment_id = identity
            if shipment_id in shipments:
                continue
            shipments[shipment_id] = Shipment(
                shipment_id=shipment_id,
                order_id=order_id,
                tracking_url=entry["href"],
                title=shorten(entry.get("title", "")),
                last_seen=datetime.now(),
            )

        LOGGER.info("Found %d shipment(s) on the order overview", len(shipments))
        return list(shipments.values())

    def capture(self, shipment: Shipment) -> TrackingPage:
        """Open one tracker page and take its text."""
        with self._browser.visit(shipment.tracking_url) as page:
            return TrackingPage(
                shipment=shipment,
                url=page.url,
                page_title=page.title(),
                text=extract_text(page),
            )

    def capture_all(self, shipments: list[Shipment] | None = None) -> list[TrackingPage]:
        """Capture every shipment, pausing between pages."""
        if shipments is None:
            shipments = self.list_shipments()

        captures: list[TrackingPage] = []
        for index, shipment in enumerate(shipments):
            if index:
                delay = random.uniform(MIN_PAUSE_SECONDS, MAX_PAUSE_SECONDS)
                LOGGER.debug("Pausing %.1fs before the next tracker page", delay)
                time.sleep(delay)
            LOGGER.info("Capturing %s (%s)", shipment.shipment_id, shipment.title or "untitled")
            captures.append(self.capture(shipment))
        return captures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _configure_logging(level: int) -> None:
    # Logs go to stderr so stdout stays a clean, redirectable dump.
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _emit(capture: TrackingPage, out_dir: Path | None) -> None:
    rule = "=" * 72
    print(rule)
    print(f"shipment_id : {capture.shipment.shipment_id}")
    print(f"order_id    : {capture.shipment.order_id}")
    print(f"title       : {capture.shipment.title or '-'}")
    print(f"url         : {capture.url}")
    print(f"page_title  : {capture.page_title}")
    print(f"fetched_at  : {capture.fetched_at.isoformat(timespec='seconds')}")
    print(f"characters  : {len(capture.text)}")
    print(rule)

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{capture.shipment.shipment_id}.txt"
        target.write_text(capture.text, encoding="utf-8")
        print(f"-> {target}")
    else:
        print(capture.text or "(no text extracted)")
    print()


def main(argv: list[str] | None = None) -> int:
    config = Config.load()

    parser = argparse.ArgumentParser(
        prog="paketbote",
        description="Dump Amazon tracking pages as raw text.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="capture every active shipment's tracker page (the default)",
    )
    parser.add_argument(
        "--orders-only",
        action="store_true",
        help="only list what the order overview reveals, open no tracker pages",
    )
    parser.add_argument(
        "--out",
        type=Path,
        metavar="DIR",
        help="write each capture to DIR/<shipment_id>.txt as a phase 3 fixture",
    )
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome debugging endpoint")
    parser.add_argument(
        "--log-level",
        choices=sorted(LOG_LEVELS),
        default=config.log_level,
    )
    args = parser.parse_args(argv)

    _configure_logging(LOG_LEVELS.get(args.log_level, logging.INFO))

    try:
        with AttachedBrowser(args.cdp_url) as browser:
            scraper = Scraper(config, browser)
            shipments = scraper.list_shipments()

            if not shipments:
                print("No shipments with a tracking link on the order overview.")
                return 0

            if args.orders_only:
                for shipment in shipments:
                    print(f"{shipment.shipment_id}\t{shipment.title or '-'}\t{shipment.tracking_url}")
                return 0

            captures = scraper.capture_all(shipments)

    except LoginRequired as err:
        LOGGER.error("Amazon wants a human: %s", err)
        LOGGER.error("Open the add-on panel, finish the challenge, then run this again.")
        return 2
    except BrowserUnavailable as err:
        LOGGER.error("%s", err)
        return 3

    for capture in captures:
        _emit(capture, args.out)

    if args.out is not None:
        print(f"{len(captures)} capture(s) written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
