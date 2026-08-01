"""What makes a mail look like a shipping notice, in several languages.

Kept as data rather than scattered through the code: a new language or a new
carrier is a line here, not a change to the logic. Everything is matched
lower-case and without diacritics, so `Versandbestätigung` and
`VERSANDBESTAETIGUNG` both land.

The terms are deliberately broad. A mail wrongly flagged as shipping costs one
pass through the extractor and is then dropped for having no tracking number;
a mail wrongly ignored is a parcel that never appears.
"""

from __future__ import annotations

import re
import unicodedata

# Words that suggest a mail is about a shipment. Grouped by language purely so
# they stay maintainable — matching does not care which list a word came from.
TERMS: dict[str, tuple[str, ...]] = {
    "de": (
        "sendung", "sendungsnummer", "sendungsverfolgung", "sendungsstatus",
        "paket", "paketverfolgung", "packchen", "päckchen",
        "versand", "versandt", "versandbestatigung", "verschickt", "versendet",
        "lieferung", "geliefert", "zustellung", "zugestellt", "unterwegs",
        "auf dem weg", "verfolgen", "nachverfolgung", "trackingnummer",
        "frachtbrief", "abholbereit", "zustellversuch", "paketshop",
    ),
    "en": (
        "shipment", "shipped", "shipping confirmation", "dispatched", "despatched",
        "parcel", "package", "tracking", "tracking number", "track your",
        "delivery", "delivered", "out for delivery", "on its way", "on the way",
        "waybill", "consignment", "ready for collection", "delivery attempt",
    ),
    "fr": (
        "colis", "expedition", "expedie", "suivi", "numero de suivi",
        "livraison", "livre", "en cours de livraison", "bordereau",
    ),
    "nl": (
        "zending", "pakket", "verzonden", "verzending", "volgen", "trackingnummer",
        "bezorging", "bezorgd", "onderweg",
    ),
    "it": (
        "spedizione", "spedito", "pacco", "tracciamento", "numero di tracciamento",
        "consegna", "consegnato", "in consegna",
    ),
    "es": (
        "envio", "enviado", "paquete", "seguimiento", "numero de seguimiento",
        "entrega", "entregado", "en reparto",
    ),
    "pl": (
        "przesylka", "paczka", "sledzenie", "numer przesylki", "wysylka",
        "wyslano", "doreczenie", "doreczono",
    ),
}

# Every term, flattened, with the accents already stripped.
ALL_TERMS: tuple[str, ...] = ()


# Letters that are not an accented base character, so stripping combining
# marks leaves them untouched. Polish ł is the one that matters here.
TRANSLITERATE = (
    ("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
    ("ł", "l"), ("đ", "d"), ("ø", "o"), ("æ", "ae"), ("þ", "th"),
)


def fold(text: str) -> str:
    """Lower-case, accent-free, single-spaced — the form everything matches in.

    German umlauts are transliterated rather than merely stripped, so
    `Päckchen` matches `paeckchen` as well as `packchen`.
    """
    lowered = (text or "").lower()
    for special, plain in TRANSLITERATE:
        lowered = lowered.replace(special, plain)
    stripped = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in stripped if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped).strip()


ALL_TERMS = tuple(sorted({fold(term) for terms in TERMS.values() for term in terms}))


def matched_terms(text: str) -> list[str]:
    """Which shipping words appear. Empty means this reads like any other mail."""
    folded = fold(text)
    return [term for term in ALL_TERMS if term in folded]


def looks_like_shipping(text: str) -> bool:
    folded = fold(text)
    return any(term in folded for term in ALL_TERMS)
