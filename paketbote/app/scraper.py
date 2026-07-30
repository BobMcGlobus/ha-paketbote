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
from .models import OrderOverview, Shipment, TrackingPage, shorten

LOGGER = logging.getLogger(__name__)

# Link shapes that lead to a tracking page. Matching on URL rather than markup
# is the durable part; this tuple is the maintenance point when Amazon
# reorganises its order pages.
TRACKING_HREF_MARKERS = ("progress-tracker", "ship-track", "shipment-tracking")

# Containers tried in order when reading a tracker page, most specific first.
MAIN_CONTAINER_CANDIDATES = ("#pt-page-container", "main", "#a-page", "body")

RAW_TEXT_LIMIT = 20_000
CARD_TEXT_LIMIT = 2_000
ORDER_ID_RE = re.compile(r"\b[A-Z0-9]{3}-\d{7}-\d{7}\b")

# Amazon nests order cards deeply; a shallow walk finds no product link and
# every shipment ends up untitled.
CARD_WALK_MAX_DEPTH = 20

# How far to keep climbing past the first product link, to pick up the status
# header that sits above it.
CARD_WALK_EXTRA_LEVELS = 3

# Two tracker pages back to back look like a script; the plan asks for a pause.
MIN_PAUSE_SECONDS = 2.0
MAX_PAUSE_SECONDS = 5.0

# Which shipments are worth an expensive tracker request. Checked against the
# order card's own text, so the cheap tier does the filtering. Active wins over
# delivered: a multi-item order can show both at once.
ACTIVE_CARD_MARKERS = (
    "kommt heute",
    "kommt morgen",
    "ankunft",
    "zustellung heute",
    "wird heute zugestellt",
    "wird zugestellt",
    "in zustellung",
    "unterwegs",
    "versandt",
    "verspätet",
    "arriving",
    "out for delivery",
)
DELIVERED_CARD_MARKERS = (
    "zugestellt",
    "geliefert",
    "zustellung abgeschlossen",
    "delivered",
)

_COLLECT_TRACKING_LINKS_JS = """
({ markers, maxDepth, extraLevels, cardTextLimit }) => {
  const PRODUCT = 'a[href*="/dp/"], a[href*="/gp/product/"]';
  const isTracking = (a) => markers.some((m) => a.href.includes(m));
  const countTracking = (el) =>
    Array.from(el.querySelectorAll('a[href]')).filter(isTracking).length;

  const seen = new Set();
  const out = [];

  for (const anchor of document.querySelectorAll('a[href]')) {
    if (!isTracking(anchor)) continue;
    const href = anchor.href;
    if (seen.has(href)) continue;
    seen.add(href);

    // Find the order card by climbing. The nearest ancestor holding a product
    // link is too narrow -- the delivery status ("Zugestellt am ...") usually
    // sits in a header above it -- so keep climbing a few levels further. The
    // boundary is an ancestor that swallows a second tracking link: that is
    // the order list, not one card.
    let node = anchor;
    let card = null;
    let title = '';
    let widened = 0;

    for (let depth = 0; depth < maxDepth && node; depth += 1) {
      if (countTracking(node) > 1) break;
      if (node.querySelector(PRODUCT)) {
        if (!card) title = (node.querySelector(PRODUCT).textContent || '').trim();
        card = node;
        widened += 1;
        if (widened > extraLevels) break;
      }
      node = node.parentElement;
    }

    const cardText = card ? (card.innerText || '').slice(0, cardTextLimit) : '';
    out.push({ href, title, cardText });
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


def looks_active(card_text: str) -> bool:
    """Is this shipment worth an expensive tracker request?

    Fails open: a card whose wording we do not recognise counts as active, so a
    vocabulary change costs requests rather than missed deliveries.
    """
    lowered = (card_text or "").lower()
    if any(marker in lowered for marker in ACTIVE_CARD_MARKERS):
        return True
    return not any(marker in lowered for marker in DELIVERED_CARD_MARKERS)


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

    def read_overview(self) -> OrderOverview:
        """The cheap tier: one page load, giving both raw text and shipments."""
        with self._browser.visit(self._config.order_history_url) as page:
            found = page.evaluate(
                _COLLECT_TRACKING_LINKS_JS,
                {
                    "markers": list(TRACKING_HREF_MARKERS),
                    "maxDepth": CARD_WALK_MAX_DEPTH,
                    "extraLevels": CARD_WALK_EXTRA_LEVELS,
                    "cardTextLimit": CARD_TEXT_LIMIT,
                },
            )
            text = extract_text(page)

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
                overview_text=normalise_text(entry.get("cardText", "")),
                last_seen=datetime.now(),
            )

        LOGGER.info("Found %d shipment(s) on the order overview", len(shipments))
        return OrderOverview(text=text, shipments=list(shipments.values()))

    @staticmethod
    def select_active(shipments: list[Shipment]) -> list[Shipment]:
        """Drop shipments the overview already reports as delivered.

        This is what keeps one poll from turning into a dozen tracker requests.
        """
        active: list[Shipment] = []
        for shipment in shipments:
            if looks_active(shipment.overview_text):
                active.append(shipment)
            else:
                LOGGER.info(
                    "Skipping %s (%s): overview says delivered",
                    shipment.shipment_id,
                    shipment.title or "untitled",
                )
        LOGGER.info("%d of %d shipment(s) still active", len(active), len(shipments))
        return active

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
        """Capture the given shipments, pausing between pages."""
        if shipments is None:
            shipments = self.select_active(self.read_overview().shipments)

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


def _emit_overview(overview: OrderOverview, out_dir: Path | None) -> None:
    """The overview is a fixture in its own right: most of what phase 3 needs
    to know is already on this page."""
    rule = "=" * 72
    print(rule)
    print("ORDER OVERVIEW")
    print(f"shipments   : {len(overview.shipments)}")
    print(f"characters  : {len(overview.text)}")
    print(rule)

    for shipment in overview.shipments:
        flag = "active" if looks_active(shipment.overview_text) else "done  "
        print(f"  [{flag}] {shipment.shipment_id}  {shipment.title or '(no title found)'}")
    print()

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "_overview.txt"
        target.write_text(overview.text, encoding="utf-8")
        print(f"-> {target}")
        cards = out_dir / "_cards.txt"
        cards.write_text(
            "\n\n".join(
                f"### {s.shipment_id} | {s.title or '(no title)'} | active={looks_active(s.overview_text)}"
                f"\n{s.overview_text}"
                for s in overview.shipments
            ),
            encoding="utf-8",
        )
        print(f"-> {cards}")
    else:
        print(overview.text or "(no text extracted)")
    print()


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
        "--all",
        action="store_true",
        dest="capture_all",
        help="also open shipments the overview reports as delivered",
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
            overview = scraper.read_overview()
            _emit_overview(overview, args.out)

            if not overview.shipments:
                print("No shipments with a tracking link on the order overview.")
                return 0

            if args.orders_only:
                for shipment in overview.shipments:
                    flag = "active " if looks_active(shipment.overview_text) else "done   "
                    print(f"{flag}\t{shipment.shipment_id}\t{shipment.title or '-'}")
                return 0

            selected = (
                overview.shipments if args.capture_all else scraper.select_active(overview.shipments)
            )
            if not selected:
                print("Every shipment is already delivered; no tracker pages opened.")
                return 0

            captures = scraper.capture_all(selected)

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
