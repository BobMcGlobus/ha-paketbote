"""Trying one way of asking a carrier, then the next.

A carrier can be reachable in more than one way: DHL has a documented API for
those who have a key, and the endpoint their own website uses for everyone
else. The documented one is preferred — it is the one with promises attached —
but a key that gets rejected must not leave the parcel unknown, so a refusal
falls through to the next way rather than ending the lookup.
"""

from __future__ import annotations

import logging

from .base import CarrierError, CarrierUpdate, NotFound, RateLimited

LOGGER = logging.getLogger(__name__)


class Chain:
    """The ways to ask one carrier, best first."""

    def __init__(self, name: str, members: list) -> None:
        self.name = name
        self._members = members

    @property
    def _usable(self) -> list:
        return [m for m in self._members if m.available]

    @property
    def available(self) -> bool:
        return bool(self._usable)

    @property
    def active(self):
        """The way that would be tried first, or None."""
        usable = self._usable
        return usable[0] if usable else None

    @property
    def tier(self) -> str:
        """`api` or `web`, for the interface to show how this is being read."""
        active = self.active
        return getattr(active, "tier", "api") if active else ""

    @property
    def wants_postcode(self) -> bool:
        return any(getattr(m, "wants_postcode", False) for m in self._usable)

    def fetch(self, tracking_code: str, postal_code: str = "") -> CarrierUpdate:
        problems = []
        for member in self._usable:
            try:
                return member.fetch(tracking_code, postal_code)
            except NotFound:
                # A definite "we do not know this number". Asking the same
                # carrier a second way would only spend another request.
                raise
            except (CarrierError, RateLimited) as err:
                problems.append(f"{getattr(member, 'tier', '?')}: {err}")
                LOGGER.info(
                    "%s via %s did not work (%s); trying the next way",
                    self.name, getattr(member, "tier", "?"), err,
                )

        raise CarrierError("; ".join(problems) or f"no way to ask {self.name}")

    def probe(self) -> tuple[bool, str]:
        """Report the first way that works, or why none did."""
        problems = []
        for member in self._usable:
            ok, detail = member.probe()
            if ok:
                return True, f"{getattr(member, 'tier', '?')} — {detail}"
            problems.append(f"{getattr(member, 'tier', '?')}: {detail}")
        return False, "; ".join(problems) or "no way to ask configured"
