"""Turning mail into shipments.

This is the second source alongside Amazon, and the one that covers every
other shop: whatever you order, someone sends a "your parcel is on its way"
mail, and it names the carrier and the number.

Where Amazon tells us who the parcel is for, a shop mail usually does not, so
these shipments start with only a number and a guess at the carrier. The guess
is then settled the honest way — by asking the carriers, best candidate first,
and dropping the ones that say they have never heard of the number.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..carriers import registry
from ..carriers.base import CarrierError, NotFound, RateLimited
from ..extractor import LlmUnavailable
from ..models import SOURCE_MAIL, STATUS_UNKNOWN, Shipment, sanitise_id, shorten
from . import extract, llm as mail_llm
from .client import Mailbox, MailboxState, MailError
from .message import Mail

LOGGER = logging.getLogger(__name__)

# Where the mailbox watermark is kept between runs.
NOTE_UID = "mail.last_uid"
NOTE_VALIDITY = "mail.uid_validity"


@dataclass
class MailResult:
    seen: int = 0
    flagged: int = 0
    added: int = 0
    unresolved: int = 0
    asked_llm: int = 0

    def __str__(self) -> str:
        return (f"{self.seen} read, {self.flagged} about parcels, "
                f"{self.added} added, {self.unresolved} without a usable number")


def _title_from(mail: Mail) -> str:
    """Something readable for the card until the carrier says more."""
    subject = (mail.subject or "").strip()
    if subject:
        return shorten(subject, 90)
    sender = (mail.sender or "").split("<")[0].strip().strip('"')
    return sender or "Sendung per Mail"


class MailSource:
    """Reads the mailbox and files what it finds."""

    def __init__(self, config, store, trackers: dict | None = None) -> None:
        self._config = config
        self._store = store
        self._trackers = trackers or {}
        self._mailbox = Mailbox(
            config.imap_host,
            config.imap_user,
            config.imap_password,
            port=config.imap_port,
            folder=config.imap_folder,
            use_ssl=config.imap_ssl,
        )

    @property
    def available(self) -> bool:
        return self._mailbox.available

    def probe(self) -> tuple[bool, str]:
        return self._mailbox.probe()

    # -- the carrier guess -------------------------------------------------

    def _confirm(self, candidate: extract.Candidate) -> str:
        """Ask a carrier whether this number is theirs.

        Returns the carrier key when it answers, an empty string when it says
        it has never heard of the number, and the key anyway when the carrier
        cannot be reached — an outage is not evidence either way.
        """
        chain = self._trackers.get(candidate.carrier)
        if chain is None or not chain.available:
            # Nothing to ask with. Accept the evidence we have.
            return candidate.carrier

        try:
            chain.fetch(candidate.code)
        except NotFound:
            LOGGER.info("%s does not know %s; trying the next candidate",
                        chain.name, candidate.code)
            return ""
        except (RateLimited, CarrierError) as err:
            LOGGER.info("Could not check %s with %s (%s); taking it on the evidence",
                        candidate.code, chain.name, err)
        return candidate.carrier

    def _settle(self, candidates: list[extract.Candidate]) -> extract.Candidate | None:
        """The first candidate a carrier owns up to."""
        for candidate in candidates:
            if not candidate.carrier:
                # No carrier to ask; keep it only if nothing better follows.
                continue
            if self._confirm(candidate):
                return candidate

        # Every candidate was denied, or none named a carrier. A number with
        # no carrier is still worth filing: it shows up in the interface and
        # can be corrected by hand.
        return candidates[0] if candidates else None

    # -- one mail ----------------------------------------------------------

    def _shipment_from(self, mail: Mail, candidate: extract.Candidate) -> Shipment | None:
        if self._store.shipment_with_code(candidate.code) is not None:
            LOGGER.debug("%s is already being followed", candidate.code)
            return None

        info = registry.lookup(candidate.carrier)
        url = candidate.url or (
            registry.tracking_url(candidate.carrier, candidate.code) if info else ""
        )

        return Shipment(
            shipment_id=sanitise_id(f"mail-{candidate.code}"),
            order_id="",
            tracking_url=url,
            title=_title_from(mail),
            carrier=info.name if info else (candidate.carrier or None),
            tracking_code=candidate.code,
            source=SOURCE_MAIL,
            status=STATUS_UNKNOWN,
            last_seen=mail.received or datetime.now(),
        )

    def handle(self, mail: Mail, result: MailResult) -> None:
        findings = extract.read(mail.subject, mail.body, mail.sender)
        if not findings.is_shipping:
            return
        result.flagged += 1

        candidates = findings.candidates
        if not candidates and self._config.llm_api_key:
            # Reads like a shipping notice but holds nothing we recognise.
            result.asked_llm += 1
            try:
                candidates = mail_llm.ask(self._config, mail.subject, mail.body)
            except LlmUnavailable as err:
                LOGGER.info("No model available for %r: %s", mail.subject[:60], err)

        if not candidates:
            LOGGER.info("No tracking number in %r", mail.subject[:60])
            result.unresolved += 1
            return

        chosen = self._settle(candidates)
        if chosen is None:
            result.unresolved += 1
            return

        shipment = self._shipment_from(mail, chosen)
        if shipment is None:
            return

        self._store.save(shipment)
        result.added += 1
        LOGGER.info("From mail: %s via %s (%s)", chosen.code,
                    shipment.carrier or "carrier unknown", ", ".join(chosen.why))

    # -- one pass ----------------------------------------------------------

    def poll(self) -> MailResult:
        """Read what has arrived and file any parcels in it."""
        result = MailResult()
        if not self.available:
            return result

        state = MailboxState(
            uid_validity=int(self._store.noted(NOTE_VALIDITY, "0") or 0),
            last_uid=int(self._store.noted(NOTE_UID, "0") or 0),
        )

        try:
            mails, new_state = self._mailbox.fetch_new(state)
        except MailError as err:
            LOGGER.warning("Mailbox could not be read: %s", err)
            return result

        for mail in mails:
            result.seen += 1
            try:
                self.handle(mail, result)
            except Exception as err:  # noqa: BLE001 - one mail must not stop the pass
                LOGGER.warning("Message %d could not be handled: %s", mail.uid, err)

        # Only advance the watermark once the batch is through, so a crash
        # re-reads rather than skips.
        self._store.note(NOTE_UID, str(new_state.last_uid))
        self._store.note(NOTE_VALIDITY, str(new_state.uid_validity))

        if result.seen:
            LOGGER.info("Mail: %s", result)
        return result
