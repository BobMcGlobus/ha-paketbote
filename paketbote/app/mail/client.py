"""Fetching new mail over IMAP.

Read-only by intention: the mailbox is opened with `readonly=True`, so nothing
is marked, moved or deleted. Which mails have been seen is remembered here
instead, by UID, so the mailbox stays exactly as its owner left it.

UIDs are only meaningful together with the folder's UIDVALIDITY. When a server
renumbers a folder, that value changes and the watermark is dropped rather
than trusted.
"""

from __future__ import annotations

import imaplib
import logging
import re
from dataclasses import dataclass

from .message import Mail, parse

LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 30

# One pass should not pull down a whole archive on first run.
MAX_PER_RUN = 40


class MailError(Exception):
    """The mailbox could not be read."""


@dataclass(frozen=True)
class MailboxState:
    """Where we got to last time."""

    uid_validity: int = 0
    last_uid: int = 0


def _uid_validity(connection) -> int:
    try:
        typ, data = connection.status(connection.selected_folder, "(UIDVALIDITY)")
    except (imaplib.IMAP4.error, AttributeError):
        return 0
    if typ != "OK" or not data:
        return 0
    match = re.search(rb"UIDVALIDITY\s+(\d+)", data[0] or b"")
    return int(match.group(1)) if match else 0


class Mailbox:
    """A read-only view of one IMAP folder."""

    def __init__(self, host: str, user: str, password: str, *,
                 port: int = 993, folder: str = "INBOX", use_ssl: bool = True) -> None:
        self._host = (host or "").strip()
        self._user = (user or "").strip()
        self._password = password or ""
        self._port = port
        self._folder = (folder or "INBOX").strip() or "INBOX"
        self._ssl = use_ssl

    @property
    def available(self) -> bool:
        return bool(self._host and self._user and self._password)

    def _connect(self):
        if not self.available:
            raise MailError("mailbox is not configured")
        try:
            if self._ssl:
                connection = imaplib.IMAP4_SSL(self._host, self._port,
                                               timeout=CONNECT_TIMEOUT)
            else:
                connection = imaplib.IMAP4(self._host, self._port,
                                           timeout=CONNECT_TIMEOUT)
        except OSError as err:
            raise MailError(f"could not reach {self._host}: {err}") from err

        try:
            connection.login(self._user, self._password)
        except imaplib.IMAP4.error as err:
            self._logout(connection)
            raise MailError(f"the mail server refused the login: {err}") from err

        try:
            typ, _ = connection.select(self._folder, readonly=True)
        except imaplib.IMAP4.error as err:
            self._logout(connection)
            raise MailError(f"could not open {self._folder!r}: {err}") from err
        if typ != "OK":
            self._logout(connection)
            raise MailError(f"could not open {self._folder!r}")

        connection.selected_folder = self._folder
        return connection

    @staticmethod
    def _logout(connection) -> None:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001 - closing must never be what fails
            pass

    def probe(self) -> tuple[bool, str]:
        """Whether the mailbox can be opened, and how much is in it."""
        if not self.available:
            return False, "no mailbox configured"
        try:
            connection = self._connect()
        except MailError as err:
            return False, str(err)
        try:
            typ, data = connection.search(None, "ALL")
            count = len(data[0].split()) if typ == "OK" and data and data[0] else 0
            return True, f"{self._folder} opened, {count} messages"
        finally:
            self._logout(connection)

    def fetch_new(self, state: MailboxState) -> tuple[list[Mail], MailboxState]:
        """Everything that arrived since the watermark, oldest first."""
        connection = self._connect()
        try:
            validity = _uid_validity(connection)
            since = state.last_uid
            if validity and state.uid_validity and validity != state.uid_validity:
                LOGGER.warning(
                    "%s was renumbered by the server; reading from the end again",
                    self._folder,
                )
                since = 0

            typ, data = connection.uid("search", None, f"UID {since + 1}:*")
            if typ != "OK":
                raise MailError("the server refused the search")

            uids = [int(u) for u in (data[0] or b"").split() if int(u) > since]
            if not uids:
                return [], MailboxState(validity or state.uid_validity, state.last_uid)

            # On a first run, only the newest few: the point is what arrives
            # from now on, not the entire history of the mailbox.
            if since == 0 and len(uids) > MAX_PER_RUN:
                LOGGER.info("First run: reading the newest %d of %d messages",
                            MAX_PER_RUN, len(uids))
                uids = uids[-MAX_PER_RUN:]
            uids = uids[:MAX_PER_RUN]

            mails = []
            for uid in uids:
                typ, payload = connection.uid("fetch", str(uid), "(BODY.PEEK[])")
                if typ != "OK" or not payload or not isinstance(payload[0], tuple):
                    LOGGER.debug("Could not read message %d; skipping it", uid)
                    continue
                try:
                    mails.append(parse(payload[0][1], uid))
                except Exception as err:  # noqa: BLE001 - one bad mail, not all
                    LOGGER.warning("Message %d could not be read: %s", uid, err)

            highest = max(uids)
            return mails, MailboxState(validity or state.uid_validity, highest)
        finally:
            self._logout(connection)
