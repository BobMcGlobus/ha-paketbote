"""Shared plumbing for carriers that hand out short-lived tokens.

UPS and FedEx both want a client id and secret exchanged for a bearer token.
The token is cached until shortly before it expires, so a burst of shipments
costs one token, not one per parcel.
"""

from __future__ import annotations

import logging
import time as time_module

import requests

from .base import CarrierError

LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 25

# Renew a little early rather than discover the expiry mid-request.
TOKEN_SAFETY_SECONDS = 60


class TokenCarrier:
    """A carrier reached with an OAuth client-credentials token."""

    name = ""
    token_url = ""

    def __init__(self, client_id: str, client_secret: str, store=None) -> None:
        # Credentials pasted from a portal often carry whitespace.
        self._client_id = (client_id or "").strip()
        self._client_secret = (client_secret or "").strip()
        self._store = store
        self._token = ""
        self._token_expires = 0.0

    @property
    def available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _token_request(self) -> requests.Response:
        """How this carrier wants the credentials presented."""
        raise NotImplementedError

    def token(self) -> str:
        if self._token and time_module.monotonic() < self._token_expires:
            return self._token
        if not self.available:
            raise CarrierError(f"no {self.name} credentials configured")

        try:
            response = self._token_request()
        except requests.RequestException as err:
            raise CarrierError(f"could not reach {self.name}: {err}") from err

        if response.status_code in (400, 401, 403):
            raise CarrierError(
                f"{self.name} rejected the credentials (HTTP {response.status_code})"
            )
        if not response.ok:
            raise CarrierError(f"{self.name} replied {response.status_code} to the token request")

        try:
            payload = response.json()
        except ValueError as err:
            raise CarrierError(f"{self.name} sent no JSON for the token: {err}") from err

        token = payload.get("access_token")
        if not token:
            raise CarrierError(f"{self.name} returned no access token")

        try:
            lifetime = float(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            lifetime = 3600.0

        self._token = str(token)
        self._token_expires = time_module.monotonic() + max(30.0, lifetime - TOKEN_SAFETY_SECONDS)
        LOGGER.debug("%s token good for %.0f seconds", self.name, lifetime)
        return self._token

    def probe(self) -> tuple[bool, str]:
        """Fetching a token is the whole test: it needs no shipment to exist."""
        if not self.available:
            return False, "no credentials configured"
        try:
            self.token()
        except CarrierError as err:
            return False, str(err)
        return True, "credentials accepted"

    def _count(self) -> None:
        if self._store is not None:
            self._store.count_carrier_request(self.name.lower())
