"""Mail as a source of shipments.

Amazon is read from the browser; everything else announces itself by mail.
One shipping notice from any shop carries the same two facts the tracker page
does — a number and a carrier — and needs no login to get at.
"""

from .client import Mailbox, MailboxState, MailError
from .message import Mail, parse
from .source import MailResult, MailSource

__all__ = [
    "Mail",
    "MailError",
    "MailResult",
    "MailSource",
    "Mailbox",
    "MailboxState",
    "parse",
]
