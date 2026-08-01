"""How to tell from a mail which carrier is going to bring the parcel.

Three kinds of evidence, in descending order of trust:

1. **The host in a tracking link.** A link to `my.dpd.de` is DPD, full stop.
2. **The carrier named in the text.** Strong, but shops mention carriers they
   are not using ("nicht per DHL versandt").
3. **The shape of the tracking number.** Mostly weak: DPD, Hermes and GLS all
   use fourteen digits, so the shape alone cannot separate them. A few shapes
   *are* decisive — nothing but UPS looks like `1Z` followed by sixteen
   characters — and those are scored accordingly.

Because the weak evidence genuinely cannot decide, the caller takes the best
few candidates and asks the carriers themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# What each kind of evidence is worth. A single host match should beat any
# amount of guessing from digits.
WEIGHT_HOST = 100
WEIGHT_NAME = 40
WEIGHT_CODE_STRONG = 45
WEIGHT_CODE_WEAK = 8


@dataclass(frozen=True)
class Signature:
    key: str
    hosts: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    # (pattern, decisive) — decisive means the shape belongs to this carrier alone.
    codes: tuple[tuple[str, bool], ...] = ()
    patterns: tuple[tuple[re.Pattern, bool], ...] = field(default=(), compare=False)

    def compiled(self) -> tuple[tuple[re.Pattern, bool], ...]:
        return tuple((re.compile(p, re.I), strong) for p, strong in self.codes)


SIGNATURES: tuple[Signature, ...] = (
    Signature(
        "dhl",
        hosts=("dhl.de", "dhl.com", "nolp.dhl.de", "dhlparcel", "deutschepost.de"),
        names=("dhl", "deutsche post"),
        codes=(
            # 00340/00341 plus fifteen digits is DHL's German parcel number.
            (r"\b0034[01]\d{15}\b", True),
            (r"\bJJD\d{15,18}\b", True),
            (r"\bJVGL\w{10,}\b", True),
            (r"\b\d{12}\b", False),
        ),
    ),
    Signature(
        "dpd",
        hosts=("dpd.de", "my.dpd.de", "tracking.dpd", "dpdgroup.com", "dpd.com"),
        names=("dpd",),
        codes=((r"\b0\d{13}\b", False), (r"\b\d{14}\b", False)),
    ),
    Signature(
        "hermes",
        hosts=("myhermes.de", "my-deliveries.de", "hermesworld"),
        names=("hermes",),
        codes=((r"\b\d{14}\b", False), (r"\bH\d{13}\b", True)),
    ),
    Signature(
        "gls",
        hosts=("gls-group", "gls-pakete.de", "gls-one.de"),
        names=("gls",),
        codes=((r"\b\d{11,12}\b", False),),
    ),
    Signature(
        "ups",
        hosts=("ups.com",),
        names=("ups", "united parcel"),
        codes=((r"\b1Z[0-9A-Z]{16}\b", True),),
    ),
    Signature(
        "fedex",
        hosts=("fedex.com", "tnt.com"),
        names=("fedex", "federal express", "tnt"),
        codes=((r"\b\d{15}\b", False), (r"\b\d{20}\b", False), (r"\b\d{22}\b", False)),
    ),
    Signature(
        "amzl",
        hosts=("amazon.de/progress-tracker", "amazon.com/progress-tracker",
               "amazon.de/gp/your-account/ship-track"),
        names=("amazon logistics", "amzl"),
        codes=((r"\bTBA\d{9,12}\b", True), (r"\bDE\d{10,12}\b", False)),
    ),
)

BY_KEY = {signature.key: signature for signature in SIGNATURES}

# Compiled once; the patterns are used on every mail.
COMPILED = {signature.key: signature.compiled() for signature in SIGNATURES}

# Every host fragment, for the cheap "is this about a parcel at all" check.
ALL_HOSTS: tuple[str, ...] = tuple(
    host for signature in SIGNATURES for host in signature.hosts
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)

# Tracking links carry the number in the path or the query; these are the
# parameter names carriers actually use.
_CODE_PARAM_RE = re.compile(
    r"(?:piececode|trackingnumber|tracknum|trackingid|parcelno|match|"
    r"sendungsnummer|idc|tracking_number|trackid|shipmentid|code|nummer)"
    r"=([A-Za-z0-9]{6,35})",
    re.I,
)


def find_urls(text: str) -> list[str]:
    """Every link in the mail, in the order they appear."""
    seen, urls = set(), []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def carrier_for_url(url: str) -> str:
    """Which carrier a link points at, or an empty string."""
    lowered = (url or "").lower()
    for signature in SIGNATURES:
        if any(host in lowered for host in signature.hosts):
            return signature.key
    return ""


def code_in_url(url: str) -> str:
    """The tracking number a link carries, from its query or its last path part."""
    match = _CODE_PARAM_RE.search(url or "")
    if match:
        return match.group(1)

    # Otherwise the number tends to be the last meaningful path segment.
    path = (url or "").split("?")[0].split("#")[0]
    for part in reversed([p for p in path.split("/") if p]):
        if re.fullmatch(r"[A-Za-z0-9]{8,35}", part) and any(ch.isdigit() for ch in part):
            return part
    return ""


def codes_in_text(text: str) -> list[tuple[str, str, bool]]:
    """Tracking-number-shaped strings, as (code, carrier key, decisive)."""
    found = []
    seen = set()
    for key, patterns in COMPILED.items():
        for pattern, strong in patterns:
            for match in pattern.finditer(text or ""):
                code = match.group(0)
                if (code, key) not in seen:
                    seen.add((code, key))
                    found.append((code, key, strong))
    return found


def names_in_text(folded: str) -> list[str]:
    """Carriers mentioned by name in the already-folded text."""
    return [
        signature.key
        for signature in SIGNATURES
        if any(name in folded for name in signature.names)
    ]
