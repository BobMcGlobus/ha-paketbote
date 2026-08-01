"""Reading a mail: is it about a parcel, and if so, whose and which number.

Everything here is pure. A mail goes in as subject, body and sender; a ranked
list of candidates comes out. Nothing is fetched, so the whole thing is
testable against saved messages.

The ranking exists because the evidence is genuinely ambiguous: DPD, Hermes
and GLS all number their parcels with fourteen digits. Rather than guess, the
best few candidates are handed to the caller, which asks the carriers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import signatures
from .keywords import fold, matched_terms

LOGGER = logging.getLogger(__name__)

# How many candidates are worth the requests it costs to check them.
MAX_CANDIDATES = 3

# Shop mails quote order numbers, customer numbers and invoice numbers that
# look just like tracking numbers. A number sitting right after one of these
# is very likely not the one we want.
_NEAR_MISS_RE = re.compile(
    r"(bestellnummer|bestell-nr|auftragsnummer|rechnungsnummer|rechnungs-nr|"
    r"kundennummer|kunden-nr|order number|order no|invoice|vat|ust-idnr|iban|"
    r"telefon|phone|artikelnummer)\W{0,20}$",
    re.I,
)


@dataclass
class Candidate:
    """One guess at what to track."""

    code: str
    carrier: str = ""
    url: str = ""
    score: int = 0
    why: tuple[str, ...] = field(default=())

    def __repr__(self) -> str:  # keeps log lines readable
        return f"<{self.code} {self.carrier or '?'} score={self.score}>"


@dataclass
class MailFindings:
    is_shipping: bool
    candidates: list[Candidate]
    terms: list[str]
    urls: list[str]

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


def _preceded_by_a_near_miss(text: str, position: int) -> bool:
    """Whether the words just before this number say it is something else."""
    return bool(_NEAR_MISS_RE.search(text[max(0, position - 60):position]))


def _looks_like_a_reference(code: str, body: str) -> bool:
    for match in re.finditer(re.escape(code), body):
        if not _preceded_by_a_near_miss(body, match.start()):
            return False  # at least one mention is unqualified
    return True


def find_candidates(subject: str, body: str, sender: str = "") -> list[Candidate]:
    """Rank what this mail might be telling us to track."""
    text = f"{subject}\n{body}"
    folded = fold(text)
    folded_sender = fold(sender)

    by_code: dict[str, Candidate] = {}

    def note(code: str, carrier: str, points: int, reason: str, url: str = "") -> None:
        code = code.strip()
        if not code:
            return
        existing = by_code.get(code)
        if existing is None:
            existing = Candidate(code=code)
            by_code[code] = existing
        existing.score += points
        if reason not in existing.why:
            existing.why = existing.why + (reason,)
        # A host is the most trustworthy thing we have, so it may overwrite an
        # earlier guess; weaker evidence only fills a blank.
        if carrier and (not existing.carrier or reason == "link"):
            existing.carrier = carrier
        if url and not existing.url:
            existing.url = url

    # 1. Links. Both the carrier and the number can come straight from these.
    urls = signatures.find_urls(text)
    for url in urls:
        carrier = signatures.carrier_for_url(url)
        code = signatures.code_in_url(url)
        if carrier and code:
            note(code, carrier, signatures.WEIGHT_HOST, "link", url)
        elif code:
            note(code, "", signatures.WEIGHT_CODE_WEAK, "url-code", url)

    # 2. Numbers in the text, scored by how telling their shape is.
    for code, carrier, decisive in signatures.codes_in_text(text):
        if _looks_like_a_reference(code, text):
            LOGGER.debug("Ignoring %s: the text calls it something else", code)
            continue
        weight = signatures.WEIGHT_CODE_STRONG if decisive else signatures.WEIGHT_CODE_WEAK
        note(code, carrier if decisive else "", weight,
             "shape" if decisive else "digits")

    # 3. Carriers named in the text or in the sender's domain. This says
    #    nothing about which number, so it lifts every candidate equally —
    #    except one that a link already tied to a different carrier.
    named = signatures.names_in_text(folded)
    from_sender = signatures.names_in_text(folded_sender)
    for candidate in by_code.values():
        if "link" in candidate.why:
            continue
        if candidate.carrier and candidate.carrier in named:
            candidate.score += signatures.WEIGHT_NAME
            candidate.why += ("named",)
        elif not candidate.carrier and len(named) == 1:
            # Exactly one carrier named and nothing contradicting it.
            candidate.carrier = named[0]
            candidate.score += signatures.WEIGHT_NAME
            candidate.why += ("named",)
        if candidate.carrier and candidate.carrier in from_sender:
            candidate.score += signatures.WEIGHT_NAME // 2
            candidate.why += ("sender",)

    ranked = sorted(
        by_code.values(),
        # Longer numbers first among equals: a fourteen-digit parcel number
        # beats the five-digit postcode it was sitting next to.
        key=lambda c: (c.score, len(c.code)),
        reverse=True,
    )
    return ranked[:MAX_CANDIDATES]


def read(subject: str, body: str, sender: str = "") -> MailFindings:
    """Everything worth knowing about one mail."""
    text = f"{subject}\n{body}"
    terms = matched_terms(text)
    urls = signatures.find_urls(text)

    # A carrier's own tracking link is proof enough on its own; a shop that
    # writes only "Ihre Bestellung ist unterwegs" needs the words.
    has_carrier_link = any(signatures.carrier_for_url(url) for url in urls)
    is_shipping = bool(terms) or has_carrier_link

    candidates = find_candidates(subject, body, sender) if is_shipping else []
    return MailFindings(
        is_shipping=is_shipping,
        candidates=candidates,
        terms=terms,
        urls=urls,
    )
