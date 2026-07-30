"""Attaching to the long-lived Chrome, and noticing when Amazon wants a human.

Chrome is started by its own s6 service, never by Playwright. We attach over
CDP and reuse the browser's *existing* profile context — that is what holds the
Amazon session cookies. A freshly created context would be logged out, which is
the whole reason the browser owns its own lifetime here.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_NAV_TIMEOUT_MS = 45_000
SETTLE_TIMEOUT_MS = 15_000

# Amazon redirects to one of these when it wants a login, an OTP or a captcha.
# Matching on the path is far more durable than matching on page markup.
CHALLENGE_PATH_MARKERS = (
    "/ap/signin",
    "/ap/mfa",
    "/ap/challenge",
    "/ap/cvf",
    "/ap/accountfixup",
    "/ap/forgotpassword",
    "/errors/validatecaptcha",
)

# Deliberately narrow. Generic words like "Anmelden" appear in the header of
# every logged-in page too, so only unmistakable challenge wording counts.
CHALLENGE_TEXT_MARKERS = (
    "geben sie die zeichen ein",
    "enter the characters you see below",
    "type the characters you see in this image",
    "tut uns leid, wir müssen nur sicherstellen",
    "sorry, we just need to make sure you're not a robot",
)


class ScraperError(Exception):
    """Base class for everything this package raises on purpose."""


class BrowserUnavailable(ScraperError):
    """Chrome is not reachable over CDP."""


class LoginRequired(ScraperError):
    """Amazon put a login, MFA or captcha wall in front of the page."""

    def __init__(self, reason: str, url: str) -> None:
        super().__init__(f"{reason} (at {url})")
        self.reason = reason
        self.url = url


def detect_challenge(page: Page) -> str | None:
    """Return why this page looks like a challenge, or None if it looks fine."""
    url = (page.url or "").lower()
    for marker in CHALLENGE_PATH_MARKERS:
        if marker in url:
            return f"URL contains {marker}"

    try:
        text = page.inner_text("body", timeout=5_000)[:4_000].lower()
    except (PlaywrightError, PlaywrightTimeout):
        return None

    for marker in CHALLENGE_TEXT_MARKERS:
        if marker in text:
            return f"page text contains {marker!r}"
    return None


class AttachedBrowser:
    """A CDP attachment to the running Chrome, used as a context manager."""

    def __init__(
        self,
        cdp_url: str = DEFAULT_CDP_URL,
        nav_timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    ) -> None:
        self._cdp_url = cdp_url
        self._nav_timeout_ms = nav_timeout_ms
        self._playwright = None
        self._browser = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> "AttachedBrowser":
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_url)
        except (PlaywrightError, PlaywrightTimeout) as err:
            self._playwright.stop()
            self._playwright = None
            raise BrowserUnavailable(
                f"Cannot reach Chrome over CDP at {self._cdp_url}: {err}"
            ) from err

        if not self._browser.contexts:
            self._teardown()
            raise BrowserUnavailable("Chrome exposes no browser context")

        # contexts[0] is the profile context holding the Amazon session.
        self._context = self._browser.contexts[0]
        LOGGER.debug("Attached to Chrome at %s", self._cdp_url)
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._teardown()

    def _teardown(self) -> None:
        # Note: browser.close() is never called. Stopping the Playwright driver
        # drops the CDP connection and leaves Chrome — and the session — alone.
        self._context = None
        self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001 - teardown must not mask real errors
                LOGGER.debug("Playwright driver did not stop cleanly", exc_info=True)
            self._playwright = None

    @contextmanager
    def page(self) -> Iterator[Page]:
        """Open a tab and always close it again.

        The tab is real and visible: when navigation misbehaves you can watch
        it happen in the add-on panel.
        """
        if self._context is None:
            raise BrowserUnavailable("Browser is not attached")

        page = self._context.new_page()
        page.set_default_timeout(self._nav_timeout_ms)
        try:
            yield page
        finally:
            try:
                page.close()
            except (PlaywrightError, PlaywrightTimeout):
                LOGGER.debug("Tab did not close cleanly", exc_info=True)

    @contextmanager
    def visit(self, url: str) -> Iterator[Page]:
        """Open `url` in a fresh tab, or raise LoginRequired if Amazon blocks."""
        with self.page() as page:
            LOGGER.debug("Navigating to %s", url)
            page.goto(url, wait_until="domcontentloaded", timeout=self._nav_timeout_ms)

            # The progress tracker renders client-side; give it a chance to
            # finish, but do not treat a chatty page as a failure.
            try:
                page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_MS)
            except PlaywrightTimeout:
                LOGGER.debug("Page %s never went idle; continuing anyway", page.url)

            reason = detect_challenge(page)
            if reason:
                raise LoginRequired(reason, page.url)

            yield page
