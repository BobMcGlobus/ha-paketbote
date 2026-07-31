"""Entrypoint: one polling loop that reads Amazon and feeds Home Assistant.

Deliberately boring. The interesting decisions live in scheduler.py (when to
poll), extractor.py (how to read a page) and mqtt.py (what HA sees); this file
only sequences them and handles the ways the outside world says no.
"""

from __future__ import annotations

import json
import logging
import re
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import __version__
from .browser import AttachedBrowser, BrowserUnavailable, LoginRequired
from .carriers import CarrierError, NotFound, RateLimited
from .carriers import dhl as dhl_carrier
from .carriers.dhl import DhlTracker
from .config import Config
from .extractor import SOURCE_CSS, SOURCE_LLM, extract
from .models import (
    SOURCE_AMAZON,
    STATE_DELIVERED,
    STATUS_DELIVERED,
    Shipment,
    ShipmentFacts,
)
from .mqtt import Publisher
from .scheduler import next_interval_minutes, state_for, summarise
from .scraper import Scraper
from .state import Store
from .supervisor import SupervisorUnavailable, mqtt_credentials

LOGGER = logging.getLogger(__name__)

# Amazon asked for a human, or blocked us. Back off hard, reset on success.
CHALLENGE_BACKOFF_MINUTES = (5, 15, 60, 240)

# Consecutive cycles in which no page could be read by CSS before the
# selector-health sensor reports a problem.
CSS_FAILURE_THRESHOLD = 2

# How long a delivered parcel stays in view before it is archived.
KEEP_DELIVERED_DAYS = 3

# When archived parcels are finally dropped.
ARCHIVE_DAYS = 90

DUMP_DIR = Path("/config/dumps")

# The interface runs as its own process; these two files are how it and the
# scheduler talk to each other.
STATUS_PATH = Path("/config/status.json")
POLL_REQUEST_PATH = Path("/config/.poll-now")


class Paketbote:
    def __init__(self, config: Config, store: Store, publisher: Publisher) -> None:
        self._config = config
        self._store = store
        self._publisher = publisher
        self._running = True
        self._challenge_strikes = 0
        self._login_required = False
        self._throttled = False
        self._last_sources: list[str] = []
        self._last_poll: datetime | None = None
        self._dhl = DhlTracker(config.dhl_api_key, store)
        self._css_failures = 0

    def stop(self, *_args: object) -> None:
        LOGGER.info("Shutting down")
        self._running = False

    # -- the loop ----------------------------------------------------------

    def run_forever(self) -> None:
        self._publisher.announce_aggregates()
        for shipment in self._store.all_shipments():
            self._publisher.announce_shipment(shipment)

        while self._running:
            wait_minutes = self._tick()
            LOGGER.info("Next poll in %.1f minutes", wait_minutes)
            self._sleep(wait_minutes * 60)

    def _sleep(self, seconds: float) -> None:
        """Sleep in slices, so neither a stop signal nor the interface waits."""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            if POLL_REQUEST_PATH.exists():
                POLL_REQUEST_PATH.unlink(missing_ok=True)
                LOGGER.info("Poll requested from the interface; waking up")
                return
            time.sleep(min(2.0, deadline - time.monotonic()))

    def _tick(self) -> float:
        now = datetime.now()
        config = self._config

        used = self._store.requests_today()
        if used >= config.daily_request_cap:
            if not self._throttled:
                LOGGER.warning(
                    "Daily request cap of %d reached; holding off until midnight",
                    config.daily_request_cap,
                )
            self._throttled = True
            self._publish(now)
            return float(config.poll_idle_minutes)
        self._throttled = False

        try:
            self._poll(now)
        except LoginRequired as err:
            self._challenge_strikes = min(
                self._challenge_strikes + 1, len(CHALLENGE_BACKOFF_MINUTES)
            )
            wait = CHALLENGE_BACKOFF_MINUTES[self._challenge_strikes - 1]
            self._login_required = True
            LOGGER.error("Amazon wants a human: %s", err)
            LOGGER.error("Open the add-on panel and finish it. Retrying in %d minutes.", wait)
            self._publish(now)
            return float(wait)
        except BrowserUnavailable as err:
            LOGGER.error("%s", err)
            self._publish(now)
            return float(min(5, self._config.poll_idle_minutes))
        except Exception:  # noqa: BLE001 - a bad cycle must not kill the loop
            LOGGER.exception("Polling cycle failed")
            self._publish(now)
            return float(min(15, self._config.poll_idle_minutes))

        self._challenge_strikes = 0
        self._login_required = False
        self._last_poll = now
        self._archive(now)

        shipments = self._store.all_shipments()
        states = [s.state for s in shipments if s.state != STATE_DELIVERED]
        self._publish(now)
        return next_interval_minutes(states, now, self._config)

    # -- one pass over Amazon ---------------------------------------------

    def _poll(self, now: datetime) -> None:
        config = self._config
        today = now.date()

        with AttachedBrowser() as browser:
            scraper = Scraper(config, browser)

            overview = scraper.read_overview()
            self._store.count_requests(1)

            stored = self._store.all_shipments()
            known = {s.shipment_id: s for s in stored}
            seen = {s.shipment_id for s in overview.shipments}

            # Orders drop off the list once they age out of Amazon's window.
            # Manually added parcels were never on it and must survive.
            from_amazon = {s.shipment_id for s in stored if s.source == SOURCE_AMAZON}
            for shipment_id in from_amazon - seen:
                vanished = known[shipment_id]
                LOGGER.info("%s is no longer listed; treating it as delivered", shipment_id)
                self._mark_delivered(vanished, now)

            active = scraper.select_active(overview.shipments)
            sources: list[str] = []

            for shipment in active:
                budget_left = config.daily_request_cap - self._store.requests_today()
                if budget_left <= 0:
                    LOGGER.warning("Request cap reached mid-cycle; stopping here")
                    break

                facts = self._read(scraper, shipment, today)
                self._store.count_requests(1)
                sources.append(facts.source)
                if facts.css_fields:
                    self._store.record_fields(facts.css_fields, now)

                previous = known.get(shipment.shipment_id)
                self._merge(shipment, facts, previous)
                self._ask_carrier(shipment)
                shipment.state = state_for(shipment, now, config)
                shipment.last_seen = now

                self._store.save(shipment)
                self._publisher.announce_shipment(shipment)
                self._publisher.publish_shipment(shipment)

            # The overview reports these as delivered. Publish that once, then
            # take the device out of Home Assistant.
            active_ids = {s.shipment_id for s in active}
            for shipment in overview.shipments:
                if shipment.shipment_id in active_ids:
                    continue
                stored = known.get(shipment.shipment_id)
                if stored is None:
                    continue
                if stored.delivered_at is None:
                    LOGGER.info("%s is reported delivered", stored.shipment_id)
                self._mark_delivered(stored, now)

            self._refresh_manual(now)
            self._last_sources = sources

            # One page that did not finish rendering is not a markup change.
            # Only a cycle in which *every* page failed counts, and it has to
            # happen twice before the sensor cries wolf.
            if sources:
                if all(source != SOURCE_CSS for source in sources):
                    self._css_failures += 1
                else:
                    self._css_failures = 0

    def _refresh_manual(self, now: datetime) -> None:
        """Parcels added by hand have no source page — only a carrier."""
        for shipment in self._store.all_shipments():
            if shipment.source == SOURCE_AMAZON or not shipment.tracking_code:
                continue
            if shipment.delivered_at is not None:
                continue

            self._ask_carrier(shipment)
            shipment.state = state_for(shipment, now, self._config)
            shipment.last_seen = now

            if shipment.status == STATUS_DELIVERED:
                LOGGER.info("%s was delivered", shipment.shipment_id)
                self._mark_delivered(shipment, now)
                continue

            self._store.save(shipment)
            self._publisher.announce_shipment(shipment)
            self._publisher.publish_shipment(shipment)

    def _mark_delivered(self, shipment: Shipment, now: datetime) -> None:
        """Arrived. It stays in view for a few days before being archived."""
        shipment.status = STATUS_DELIVERED
        shipment.state = STATE_DELIVERED
        if shipment.delivered_at is None:
            shipment.delivered_at = now
        self._store.save(shipment)
        self._publisher.publish_shipment(shipment)

    def _archive(self, now: datetime) -> None:
        """Take delivered parcels out of Home Assistant, and eventually out of
        the database. They remain visible in the interface in between."""
        for shipment in self._store.all_shipments():
            if shipment.delivered_at is None:
                continue
            age = now - shipment.delivered_at
            if age > timedelta(days=ARCHIVE_DAYS):
                LOGGER.info("Dropping %s from the archive", shipment.shipment_id)
                self._publisher.retire_shipment(shipment.shipment_id)
                self._store.forget(shipment.shipment_id)
            elif age > timedelta(days=KEEP_DELIVERED_DAYS):
                if shipment.shipment_id in self._publisher.announced:
                    LOGGER.info("Archiving %s", shipment.shipment_id)
                    self._publisher.retire_shipment(shipment.shipment_id)

    def _read(self, scraper: Scraper, shipment: Shipment, today: date) -> ShipmentFacts:
        def reader(page, text):
            return extract(page, text, self._config, today)

        keep_html = self._config.developer_mode
        capture, facts = scraper.capture_with(shipment, reader, keep_html=keep_html)

        if facts is None:
            return ShipmentFacts()

        if self._config.developer_mode and facts.source != SOURCE_CSS:
            self._dump_failure(capture)
        return facts

    def _dump_failure(self, capture) -> None:
        """Developer mode: keep the page that the selectors could not read."""
        try:
            target = DUMP_DIR / "selector-misses"
            target.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"{stamp}-{capture.shipment.shipment_id}"
            (target / f"{name}.txt").write_text(capture.text, encoding="utf-8")
            if capture.html:
                (target / f"{name}.html").write_text(capture.html, encoding="utf-8")
            LOGGER.info("developer_mode: kept the unreadable page as %s", name)
        except OSError:
            LOGGER.debug("Could not write the selector-miss dump", exc_info=True)

    @staticmethod
    def _merge(shipment: Shipment, facts: ShipmentFacts, previous: Shipment | None) -> None:
        """Apply what was read, keeping what was known when the read was poor."""
        # The recipient comes from the overview card and the address from the
        # tracker; keep whichever we already had when the new read is empty.
        if not shipment.recipient and previous is not None:
            shipment.recipient = previous.recipient
        shipment.delivery_address = facts.delivery_address or (
            previous.delivery_address if previous else ""
        )

        if not facts.is_usable:
            if previous is not None:
                shipment.status = previous.status
                shipment.stops_remaining = previous.stops_remaining
                shipment.window_start = previous.window_start
                shipment.window_end = previous.window_end
                shipment.expected_date = previous.expected_date
                shipment.carrier = previous.carrier
            return

        shipment.tracking_code = facts.tracking_code or (
            previous.tracking_code if previous else ""
        )
        shipment.status = facts.status
        shipment.stops_remaining = facts.stops_remaining
        shipment.window_start = facts.window_start
        shipment.window_end = facts.window_end
        shipment.expected_date = facts.expected_date
        shipment.carrier = facts.carrier or (previous.carrier if previous else None)

    def _ask_carrier(self, shipment: Shipment) -> None:
        """Let the carrier answer where the parcel is, when it can.

        DHL knows more about a DHL parcel than Amazon's tracker does, and
        asking them costs no Amazon request at all.
        """
        if not shipment.tracking_code or not self._dhl.available:
            return
        if not dhl_carrier.handles(shipment.carrier):
            return

        try:
            update = self._dhl.fetch(shipment.tracking_code, _postal_code(shipment))
        except NotFound:
            LOGGER.debug("DHL does not know %s yet", shipment.tracking_code)
            return
        except RateLimited as err:
            LOGGER.warning("%s", err)
            return
        except CarrierError as err:
            LOGGER.warning("DHL lookup failed: %s", err)
            return

        shipment.status = update.status
        if update.expected_date:
            shipment.expected_date = update.expected_date
        if update.window_start:
            shipment.window_start = update.window_start
        if update.window_end:
            shipment.window_end = update.window_end

        LOGGER.info(
            "DHL: %s is %s%s",
            shipment.shipment_id,
            update.status,
            f" ({update.description})" if update.description else "",
        )

    # -- what Home Assistant sees -----------------------------------------

    def _publish(self, now: datetime) -> None:
        shipments = self._store.all_shipments()

        # Anything deleted in the interface is still announced to Home
        # Assistant until it is taken back out.
        current = {s.shipment_id for s in shipments}
        for orphan in self._publisher.announced - current:
            LOGGER.info("%s is gone from the database; retiring it", orphan)
            self._publisher.retire_shipment(orphan)

        summary = summarise(shipments, now, self._config)

        summary.update(
            {
                "letzter_abruf": self._last_poll.astimezone().isoformat() if self._last_poll else None,
                "login_erforderlich": self._login_required,
                "gedrosselt": self._throttled,
                "extraktionsmethode": self._extraction_method(),
                "selektoren_defekt": self._css_failures >= CSS_FAILURE_THRESHOLD,
                "requests_heute": self._store.requests_today(),
            }
        )

        self._publisher.publish_summary(summary)
        self._publisher.publish_shipments(
            {
                "count": len([s for s in shipments if s.state != STATE_DELIVERED]),
                "updated": now.astimezone().isoformat(),
                "shipments": [_shipment_attributes(s) for s in shipments if s.state != STATE_DELIVERED],
            }
        )
        _write_status(summary)
        self._publisher.publish_health(
            {
                "felder": self._store.field_health(),
                "letzte_quellen": self._last_sources,
                "version": __version__,
            }
        )

    def _extraction_method(self) -> str:
        sources = set(self._last_sources)
        if not sources:
            return "unbekannt"
        if sources == {SOURCE_CSS}:
            return SOURCE_CSS
        if SOURCE_LLM in sources and SOURCE_CSS in sources:
            return "mixed"
        if SOURCE_LLM in sources:
            return SOURCE_LLM
        return "none"


def _shipment_attributes(shipment: Shipment) -> dict:
    """One shipment, flat enough for a Lovelace card to render directly."""
    return {
        "id": shipment.shipment_id,
        "title": shipment.title,
        "recipient": shipment.recipient,
        "status": shipment.status,
        "state": shipment.state,
        "carrier": shipment.carrier,
        "stops_remaining": shipment.stops_remaining,
        "window_start": shipment.window_start.isoformat() if shipment.window_start else None,
        "window_end": shipment.window_end.isoformat() if shipment.window_end else None,
        "expected_date": shipment.expected_date.isoformat() if shipment.expected_date else None,
        "tracking_url": shipment.tracking_url,
    }


def _write_status(summary: dict) -> None:
    """Hand the current picture to the interface process."""
    try:
        STATUS_PATH.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    except OSError:
        LOGGER.debug("Could not write the status file", exc_info=True)


def _postal_code(shipment: Shipment) -> str:
    """DHL only reveals the delivery window to the recipient, proven by postcode."""
    match = re.search(r"\b(\d{5})\b", shipment.delivery_address or "")
    return match.group(1) if match else ""


def main() -> int:
    config = Config.load()
    logging.basicConfig(
        level=config.python_log_level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    LOGGER.info("Paketbote %s starting", __version__)

    try:
        credentials = mqtt_credentials()
    except SupervisorUnavailable as err:
        LOGGER.error("No MQTT broker: %s", err)
        LOGGER.error("Install the Mosquitto add-on and add the MQTT integration, then restart.")
        return 1

    store = Store()
    publisher = Publisher(credentials, __version__)
    try:
        publisher.connect()
    except Exception as err:  # noqa: BLE001 - a broker that refuses is fatal
        LOGGER.error("Cannot reach the MQTT broker: %s", err)
        return 1

    app = Paketbote(config, store, publisher)
    signal.signal(signal.SIGTERM, app.stop)
    signal.signal(signal.SIGINT, app.stop)

    try:
        app.run_forever()
    finally:
        publisher.disconnect()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
