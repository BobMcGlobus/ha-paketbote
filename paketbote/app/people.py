"""Recognising that two spellings mean the same person or the same address.

A household shares one Amazon account, and the same recipient turns up as
"Jonas Althoff", "jonas althoff", "Herr Jonas Althoff" and "Althoff, Jonas"
depending on who typed the order. Without folding those together, filtering by
recipient is useless and DHL gets asked with the wrong postcode.

Deliberately conservative: it drops honorifics and middle names, and leaves
everything else alone. Merging two people who merely share a surname would be
worse than showing one of them twice.
"""

from __future__ import annotations

import re
import unicodedata

# Dropped before comparing. Kept short on purpose — every entry here is a way
# for two different people to be mistaken for one.
HONORIFICS = {
    "dr", "prof", "dipl", "ing", "med", "herr", "frau", "hr", "fr",
    "mr", "mrs", "ms", "miss", "familie", "fam", "family",
}

_SPLIT = re.compile(r"[^0-9A-Za-zÄÖÜäöüß]+")
_POSTCODE = re.compile(r"\b(\d{5})\b")


def normalise_name(raw: str | None) -> str:
    """A comparison key for a recipient name.

    "Herr Dr. Jonas Peter Althoff" and "althoff, jonas" both become
    "jonas althoff".
    """
    if not raw:
        return ""

    text = unicodedata.normalize("NFC", raw).strip().lower()

    # "Althoff, Jonas" is the same person as "Jonas Althoff".
    if "," in text:
        family, _, given = text.partition(",")
        text = f"{given} {family}"

    tokens = [token for token in _SPLIT.split(text) if token]
    tokens = [token for token in tokens if token not in HONORIFICS]
    if not tokens:
        return ""

    # Middle names come and go between orders; first and last carry the person.
    if len(tokens) > 2:
        tokens = [tokens[0], tokens[-1]]
    return " ".join(tokens)


def same_person(left: str | None, right: str | None) -> bool:
    key = normalise_name(left)
    return bool(key) and key == normalise_name(right)


def display_name(candidates: list[str]) -> str:
    """The nicest spelling among the ones seen for one person."""
    named = [c.strip() for c in candidates if c and c.strip()]
    if not named:
        return ""

    def has_honorific(name: str) -> bool:
        return any(token in HONORIFICS for token in _SPLIT.split(name.lower()) if token)

    # "Jonas Althoff" reads better than "Herr Jonas Althoff", so plain
    # spellings win outright.
    plain = [c for c in named if not has_honorific(c)]
    pool = plain or named

    # Among those, prefer the properly capitalised one over an all-lowercase
    # version, and the longer one when that does not decide it.
    return sorted(pool, key=lambda c: (sum(ch.isupper() for ch in c), len(c)), reverse=True)[0]


def postcode_of(address: str | None) -> str:
    """The German postal code in a free-form address, if there is one."""
    if not address:
        return ""
    match = _POSTCODE.search(address)
    return match.group(1) if match else ""


def normalise_address(raw: str | None) -> str:
    """A comparison key for an address: postcode plus street number."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFC", raw).strip().lower()
    postcode = postcode_of(text)
    tokens = [token for token in _SPLIT.split(text) if token]
    # Street numbers separate flats in the same building well enough, and the
    # rest of the line varies too much to compare literally.
    numbers = [token for token in tokens if any(ch.isdigit() for ch in token)]
    numbers = [token for token in numbers if token != postcode]
    return " ".join(filter(None, [postcode, *numbers[:1]]))
