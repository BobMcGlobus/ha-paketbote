"""Turning a raw mail into plain text we can search.

Shop mails are almost always HTML, and the tracking link is usually only in an
`href` — never in the visible text, which says "Sendung verfolgen". So the
links are pulled out separately and appended, rather than being lost with the
markup.
"""

from __future__ import annotations

import email
import email.policy
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header

LOGGER = logging.getLogger(__name__)

# Enough to hold any shipping notice; stops a newsletter from filling memory.
MAX_BODY_CHARS = 60_000

_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Mail:
    uid: int
    subject: str
    sender: str
    body: str
    received: datetime | None = None

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}"


def _decode(value: str | None) -> str:
    """Undo RFC 2047 encoding, without letting a malformed header raise."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return value


def html_to_text(markup: str) -> str:
    """Visible text plus every link, because the link is the point."""
    links = _HREF_RE.findall(markup or "")
    stripped = _TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", markup or ""))
    text = re.sub(r"[ \t]+", " ", html.unescape(stripped))
    if links:
        text += "\n" + "\n".join(html.unescape(link) for link in links)
    return text


def _part_text(part) -> str:
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001 - a broken part must not stop the rest
        return ""
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def parse(raw: bytes, uid: int = 0) -> Mail:
    """A raw message as fetched over IMAP."""
    message = email.message_from_bytes(raw, policy=email.policy.compat32)

    parts = []
    for part in message.walk():
        content_type = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        if content_type == "text/plain":
            parts.append(_part_text(part))
        elif content_type == "text/html":
            parts.append(html_to_text(_part_text(part)))

    body = "\n".join(p for p in parts if p)[:MAX_BODY_CHARS]

    received = None
    if message.get("Date"):
        try:
            received = email.utils.parsedate_to_datetime(message["Date"])
        except (TypeError, ValueError):
            received = None

    return Mail(
        uid=uid,
        subject=_decode(message.get("Subject")),
        sender=_decode(message.get("From")),
        body=body,
        received=received,
    )
