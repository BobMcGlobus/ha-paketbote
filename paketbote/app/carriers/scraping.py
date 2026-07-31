"""Shared plumbing for carriers we read off their own website.

These need no credentials, so they are always "available". What they cost
instead is fragility: nobody promises these responses stay the same. Every
field is therefore read defensively, and a shape we do not recognise turns
into "unknown" rather than an exception.
"""

from __future__ import annotations

import logging
import time as time_module

import requests

from .base import CarrierError

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 25

# Nobody publishes a limit for these, so we set our own and stay well under
# what a person clicking refresh would produce.
MIN_SECONDS_BETWEEN_CALLS = 3.0

# The tracking pages answer their own front-end; asking as something else
# invites a block.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "de-DE,de;q=0.9",
}


class WebTracker:
    """A carrier read from its public tracking endpoint."""

    name = ""
    tier = "web"

    # Only DHL trades a postal code for the delivery window.
    wants_postcode = False

    def __init__(self, store=None) -> None:
        self._store = store
        self._last_call = 0.0

    @property
    def available(self) -> bool:
        """No credentials to be missing."""
        return True

    @property
    def budget_key(self) -> str:
        """Counted apart from the documented API.

        DHL's 250 calls a day apply to the API key, not to their website.
        Putting both in one bucket would let website reads throttle a key that
        is perfectly fine.
        """
        return f"{self.name.lower()}_web"

    def _wait_for_slot(self) -> None:
        elapsed = time_module.monotonic() - self._last_call
        if self._last_call and elapsed < MIN_SECONDS_BETWEEN_CALLS:
            time_module.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None):
        self._wait_for_slot()
        try:
            return requests.get(
                url,
                params=params or {},
                headers={**HEADERS, **(headers or {})},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as err:
            raise CarrierError(f"{self.name} request failed: {err}") from err
        finally:
            self._last_call = time_module.monotonic()
            if self._store is not None:
                self._store.count_carrier_request(self.budget_key)

    def probe(self) -> tuple[bool, str]:
        """There is nothing to authenticate; the test is that it answers."""
        raise NotImplementedError
