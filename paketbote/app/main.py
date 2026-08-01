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
from .carriers import trackers as carrier_trackers
from .mail import MailSource
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
from .scheduler import affordable_interval, next_interval_minutes, state_for, summarise
from .scraper import Scraper
from .state import Store
from .supervisor import SupervisorUnavailable, mqtt_credentials

LOGGER = logging.getLogger(__name__)

# Amazon asked for a human, or blocked us. Back off hard, reset on success.
CHALLENGE_BACKOFF_MINUTES = (5, 15, 60, 240)

# Consecutive cycles in which no page could be read by CSS before the
# selector-health sensor reports a problem.
CSS_FAILURE_THRESHOLD = 2

# When archived parcels are finally dropped.
ARCHIVE_DAYS = 90

# Each extra guess costs a carrier request, so keep it to a household's worth.
MAX_POSTCODE_ATTEMPTS = 3

# A shipment absent from one poll means nothing: a freshly placed order shows
# up late, and a page that did not finish rendering looks exactly the same.
MISSING_POLLS_BEFORE_DELIVERED = 3

# While throttled, look again this often in case the cap was raised.
THROTTLED_RECHECK_MINUTES = 10

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
        self._carrier_credentials = carrier_trackers.credentials(config)
        self._trackers = carrier_trackers.build(config, store)
        self._css_failures = 0
        self._last_mail_poll: datetime | None = None

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

        # Re-read every cycle: the interface can change settings while this
        # process runs, and telling the user they apply "next poll" has to be
        # true.
        self._config = Config.load()
        config = self._config
        credentials = carrier_trackers.credentials(config)
        if credentials != self._carrier_credentials:
            self._carrier_credentials = credentials
            self._trackers = carrier_trackers.build(config, self._store)
            for tracker in self._trackers.values():
                LOGGER.info(
                    "%s lookups %s",
                    tracker.name,
                    "enabled" if tracker.available else "disabled",
                )

        self._poll_mail(now)

        used = self._store.requests_today()
        if used >= config.daily_request_cap:
            if not self._throttled:
                LOGGER.warning(
                    "Daily request cap of %d reached; holding off until midnight",
                    config.daily_request_cap,
                )
            self._throttled = True
            self._publish(now)
            # Check back sooner than the idle rhythm: the cap can be raised in
            # the interface, and waiting three hours to notice is no help.
            return float(THROTTLED_RECHECK_MINUTES)
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

        minutes = next_interval_minutes(states, now, self._config)
        # One overview plus one tracker page per active shipment.
        return affordable_interval(
            minutes, now, self._config, self._store.requests_today(), 1 + len(states)
        )

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

            self._drop_renamed(stored, overview.shipments)
            stored = self._store.all_shipments()
            known = {s.shipment_id: s for s in stored}

            # Orders drop off the list once they age out of Amazon's window.
            # Manually added parcels were never on it and must survive.
            from_amazon = {s.shipment_id for s in stored if s.source == SOURCE_AMAZON}
            # A list we could not read properly must not retire anything.
            trustworthy = bool(overview.shipments) and overview.content_selector in (
                ".order-card",
                ".pt-card",
            )
            for shipment_id in from_amazon:
                vanished = known[shipment_id]
                if shipment_id in seen:
                    if vanished.missed:
                        vanished.missed = 0
                        self._store.save(vanished)
                    continue
                if not trustworthy:
                    LOGGER.debug("Order list looked unreliable; not retiring %s", shipment_id)
                    continue

                vanished.missed += 1
                if vanished.missed < MISSING_POLLS_BEFORE_DELIVERED:
                    LOGGER.info(
                        "%s was not listed (%d/%d) — waiting before calling it delivered",
                        shipment_id, vanished.missed, MISSING_POLLS_BEFORE_DELIVERED,
                    )
                    self._store.save(vanished)
                    continue

                LOGGER.info("%s has been absent %d times; treating it as delivered",
                            shipment_id, vanished.missed)
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

                self._store.learn_recipient(shipment.recipient, shipment.delivery_address)
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

            self._refresh_tracked(now)
            self._last_sources = sources

            # One page that did not finish rendering is not a markup change.
            # Only a cycle in which *every* page failed counts, and it has to
            # happen twice before the sensor cries wolf.
            if sources:
                if all(source != SOURCE_CSS for source in sources):
                    self._css_failures += 1
                else:
                    self._css_failures = 0

    def _drop_renamed(self, stored: list[Shipment], seen: list[Shipment]) -> None:
        """Remove rows left behind when a parcel changed its Amazon identity.

        Amazon adds a shipmentId to the tracking link once a parcel is
        dispatched, and older versions keyed on it — so the same parcel was
        filed twice, once before and once after dispatch. The key is now the
        package index, which is there from the start, and this clears up what
        the old rule left.

        Only a stored parcel whose order is on the list *and* whose contents
        match one that is, but under a different id, is dropped. A genuine
        second package of the same order holds different articles and is on
        the list in its own right, so it stays.
        """
        seen_ids = {s.shipment_id for s in seen}
        by_order: dict[str, list[Shipment]] = {}
        for shipment in seen:
            by_order.setdefault(shipment.order_id, []).append(shipment)

        def contents(shipment: Shipment) -> tuple:
            if shipment.items:
                return tuple(sorted(str(i.get("title", "")) for i in shipment.items))
            return (shipment.title,)

        for old in stored:
            if old.source != SOURCE_AMAZON or old.shipment_id in seen_ids:
                continue
            twin = next(
                (s for s in by_order.get(old.order_id, []) if contents(s) == contents(old)),
                None,
            )
            if twin is None:
                continue

            LOGGER.info(
                "%s is %s under Amazon's older naming; dropping the duplicate",
                old.shipment_id, twin.shipment_id,
            )
            self._store.forget(old.shipment_id)
            self._publisher.retire_shipment(old.shipment_id)

    def _refresh_tracked(self, now: datetime) -> None:
        """Parcels with no source page of their own — only a carrier.

        Everything added by hand or found in a mail: Amazon knows nothing
        about them, so the carrier is the only thing to ask.
        """
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
            elif age > timedelta(hours=max(1, self._config.keep_delivered_hours)):
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

    def _postcode_candidates(self, shipment: Shipment) -> list[str]:
        """Postcodes worth trying for this parcel, best guess first.

        One recipient can live at more than one address and one address can
        serve several people, so the parcel's own page is only the first guess.
        Capped, because each attempt costs a DHL call.
        """
        candidates: list[str] = []
        own = _postal_code(shipment)
        if own:
            candidates.append(own)
        for postcode in self._store.postcodes_for(shipment.recipient):
            if postcode not in candidates:
                candidates.append(postcode)
        return (candidates or [""])[:MAX_POSTCODE_ATTEMPTS]

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

    def _poll_mail(self, now: datetime) -> None:
        """Read the mailbox on its own rhythm, independent of Amazon.

        Deliberately outside the Amazon request cap: a mail costs nothing on
        amazon.de, and stopping mail because Amazon is throttled would hide
        parcels from every other shop.
        """
        source = MailSource(self._config, self._store, self._trackers)
        if not source.available:
            return

        due = max(1, self._config.imap_poll_minutes)
        if self._last_mail_poll is not None:
            waited = (now - self._last_mail_poll).total_seconds() / 60
            if waited < due:
                return
        self._last_mail_poll = now

        try:
            source.poll()
        except Exception:  # noqa: BLE001 - the mailbox must not stop the loop
            LOGGER.exception("Reading the mailbox failed")

    def _ask_carrier(self, shipment: Shipment) -> None:
        """Let the carrier answer where the parcel is, when it can.

        A carrier knows more about its own parcel than Amazon's tracker does,
        and asking them costs no Amazon request at all.
        """
        if not shipment.tracking_code:
            LOGGER.debug("%s has no tracking number yet", shipment.shipment_id)
            return

        key = carrier_trackers.key_for(shipment.carrier)
        if not key:
            LOGGER.debug("No module answers for %r", shipment.carrier)
            return

        tracker = self._trackers[key]
        if not tracker.available:
            LOGGER.debug("No %s credentials; not asking for %s", tracker.name,
                         shipment.shipment_id)
            return

        due = carrier_trackers.poll_minutes(self._config, key)
        if shipment.carrier_checked_at is not None:
            waited = (datetime.now() - shipment.carrier_checked_at).total_seconds() / 60
            if waited < due:
                LOGGER.debug("Asked %s about %s %.0f min ago; waiting for %d",
                             tracker.name, shipment.shipment_id, waited, due)
                return
        shipment.carrier_checked_at = datetime.now()

        # Only DHL trades a postal code for the delivery window; the others
        # answer from the tracking number alone, so one call is enough.
        if tracker.wants_postcode:
            postcodes = self._postcode_candidates(shipment)
        else:
            postcodes = [""]

        update = None
        for postcode in postcodes:
            try:
                attempt = tracker.fetch(shipment.tracking_code, postcode)
            except NotFound:
                LOGGER.debug("%s does not know %s yet", tracker.name, shipment.tracking_code)
                return
            except RateLimited as err:
                LOGGER.warning("%s", err)
                return
            except CarrierError as err:
                LOGGER.warning("%s lookup failed: %s", tracker.name, err)
                return

            update = attempt
            if attempt.window_start is not None:
                # The window only comes back when the postcode proves we are
                # the recipient, so this is the one that fits.
                if postcode:
                    self._store.note_postcode_worked(shipment.recipient, postcode)
                break

        if update is None:
            return

        shipment.status = update.status
        if update.expected_date:
            shipment.expected_date = update.expected_date
        if update.window_start:
            shipment.window_start = update.window_start
        if update.window_end:
            shipment.window_end = update.window_end

        LOGGER.info(
            "%s: %s is %s%s",
            tracker.name,
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

        summary["effective"] = {
            "daily_request_cap": self._config.daily_request_cap,
            "poll_idle_minutes": self._config.poll_idle_minutes,
            "carriers": sorted(
                key for key, tracker in self._trackers.items() if tracker.available
            ),
            "dhl": bool(self._config.dhl_api_key),
            "dhl_poll_minutes": self._config.dhl_poll_minutes,
            "read_at": now.astimezone().isoformat(),
        }
        self._publisher.publish_summary(
            {k: v for k, v in summary.items() if k != "effective"}
        )
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
