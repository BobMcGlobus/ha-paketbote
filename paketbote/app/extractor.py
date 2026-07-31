"""Turning a tracker page into facts.

CSS selectors run first because they are free, deterministic and — thanks to
Amazon's own `data-` attributes on the progress bar — language independent.
The LLM only runs when the selectors come up empty, which is the signal that
Amazon changed its markup. Every attempt records per field whether CSS
delivered; that record is what the selector-health sensor reports.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeout

from .config import Config
from .models import (
    KNOWN_STATUSES,
    STATUS_DELIVERED,
    STATUS_ORDERED,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_UNKNOWN,
    ShipmentFacts,
)
from .parsing import (
    STATUS_BY_MILESTONE,
    detect_carrier,
    parse_expected_date,
    parse_stops,
    parse_window,
    status_from_label,
)

LOGGER = logging.getLogger(__name__)

SOURCE_CSS = "css"
SOURCE_LLM = "llm"
SOURCE_NONE = "none"

# Fields whose CSS success is tracked. `stops` and `window` only exist while a
# package is actually out for delivery, so they are reported separately and a
# miss on them is not by itself a broken-selector signal.
TRACKED_FIELDS = ("status", "promise", "tracking_code", "carrier")
OPPORTUNISTIC_FIELDS = ("stops", "window")

_READ_TRACKER_JS = """
() => {
  const text = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.innerText || '').trim() : null;
  };
  const all = (sel) =>
    Array.from(document.querySelectorAll(sel)).map((e) => (e.innerText || '').trim());

  // The progress bar carries data attributes, so the current step can be read
  // without comparing any human-readable label.
  const milestones = Array.from(document.querySelectorAll('.pt-status-milestone')).map((el) => ({
    label: (el.innerText || '').trim(),
    reached: el.getAttribute('data-reached') === 'true',
    last: el.getAttribute('data-last-reached') === 'true',
    percent: parseInt(el.getAttribute('data-percent-complete') || '', 10),
  }));

  return {
    promise: text('.pt-promise-main-slot'),
    mainStatus: text('.pt-status-main-status'),
    trackingId: text('.pt-delivery-card-trackingId'),
    carrierInfo: text('#carrierRelatedInfo-container'),
    // Amazon's own class name for the address block. Language independent,
    // and the only place the recipient's town appears on the tracker.
    address: text('[class*="ddress"]'),
    canReschedule: !!document.querySelector('[class*="RESCHEDULE_DELIVERY"]'),
    // The stop count never reaches the visible text: it sits in the page's
    // own JSON and only becomes a map bubble once the map library loads.
    callout: (() => {
      const match = document.documentElement.innerHTML.match(
        /"calloutMessage"\s*:\s*"([^"]{0,120})"/);
      return match ? match[1] : null;
    })(),
    milestones,
    cardText: all('.pt-card').join('\\n'),
  };
}
"""


def _status_from_milestones(milestones: list[dict]) -> str:
    """Current step of the progress bar, by position rather than by wording."""
    if not milestones:
        return STATUS_UNKNOWN

    current = next((i for i, m in enumerate(milestones) if m.get("last")), None)
    if current is None:
        reached = [i for i, m in enumerate(milestones) if m.get("reached")]
        current = reached[-1] if reached else None
    if current is None:
        return STATUS_UNKNOWN

    if len(milestones) == len(STATUS_BY_MILESTONE):
        return STATUS_BY_MILESTONE[current]

    # Unexpected bar shape: fall back to reading the label after all.
    LOGGER.debug("Progress bar has %d steps, expected %d", len(milestones), len(STATUS_BY_MILESTONE))
    return status_from_label(milestones[current].get("label", "")) or STATUS_UNKNOWN


def _one_line(value: object) -> str:
    """Flatten the address block into something a sensor state can hold."""
    if not isinstance(value, str):
        return ""
    parts = [line.strip() for line in value.splitlines() if line.strip()]
    return ", ".join(parts)


def extract_with_css(page: Page, today: date) -> ShipmentFacts:
    """Read the tracker page through Amazon's own markup."""
    try:
        raw = page.evaluate(_READ_TRACKER_JS)
    except (PlaywrightError, PlaywrightTimeout):
        LOGGER.debug("CSS extraction could not run", exc_info=True)
        return ShipmentFacts(source=SOURCE_NONE)

    promise = (raw.get("promise") or "").strip()
    card_text = raw.get("cardText") or ""
    status = _status_from_milestones(raw.get("milestones") or [])

    # The headline status is a second, independent read of the same thing.
    if status == STATUS_UNKNOWN and raw.get("mainStatus"):
        status = status_from_label(raw["mainStatus"]) or STATUS_UNKNOWN

    tracking_code = None
    if raw.get("trackingId"):
        # "Tracking ID: DE5713482611"
        tracking_code = raw["trackingId"].split(":")[-1].strip() or None

    carrier = detect_carrier(raw.get("carrierInfo") or "")
    if carrier is None and raw.get("canReschedule"):
        # Only Amazon Logistics offers rescheduling from the tracker.
        carrier = "AMZL"

    # The stop count comes from the map callout; the visible cards never carry
    # it. Fall back to the card text in case Amazon ever prints it there.
    callout = (raw.get("callout") or "").strip()
    stops = parse_stops(callout) or parse_stops(card_text)

    # The window belongs to the promise line. Reading it from the whole card
    # would pick up any two numbers that share a dash.
    window = parse_window(promise) or parse_window(card_text)
    expected = parse_expected_date(promise or card_text, today)

    facts = ShipmentFacts(
        status=status,
        stops_remaining=stops,
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
        expected_date=expected,
        promise_text=promise,
        carrier=carrier,
        tracking_code=tracking_code,
        delivery_address=_one_line(raw.get("address")),
        source=SOURCE_CSS if status != STATUS_UNKNOWN else SOURCE_NONE,
        confidence="high" if status != STATUS_UNKNOWN else "low",
        css_fields={
            "status": status != STATUS_UNKNOWN,
            "promise": bool(promise),
            "tracking_code": bool(tracking_code),
            "carrier": bool(carrier),
            "stops": stops is not None,
            "window": window is not None,
            "callout": bool(callout),
        },
    )
    return facts


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

PROMPT = """You read the visible text of an Amazon delivery tracking page.
The text may be German or English.

Reply with JSON only. No markdown, no explanation, no code fence.

{
  "status": "ordered|shipped|out_for_delivery|delivered|exception",
  "stops_remaining": <int|null>,
  "window_start": "HH:MM"|null,
  "window_end": "HH:MM"|null,
  "expected_date": "YYYY-MM-DD"|null,
  "carrier": <string|null>,
  "confidence": "high|medium|low"
}

Rules:
- Set stops_remaining only if the page states a number of stops explicitly.
- Prefer null over guessing. Use confidence "low" if you are unsure.
- expected_date is the date the package is expected, in ISO form.
- Today is {today}.

Page text:
---
{text}
---"""


class LlmUnavailable(Exception):
    """No usable LLM is configured, or the call failed."""


def _call_gemini(config: Config, prompt: str, timeout: int) -> str:
    import requests

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.llm_model}:generateContent"
    )
    response = requests.post(
        url,
        headers={"x-goog-api-key": config.llm_api_key, "content-type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai(config: Config, prompt: str, timeout: int) -> str:
    import requests

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"authorization": f"Bearer {config.llm_api_key}"},
        json={
            "model": config.llm_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _call_anthropic(config: Config, prompt: str, timeout: int) -> str:
    import requests

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": config.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": config.llm_model,
            "max_tokens": 512,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"]


PROVIDERS = {"gemini": _call_gemini, "openai": _call_openai, "anthropic": _call_anthropic}

LLM_TIMEOUT_SECONDS = 30


def parse_llm_json(payload: str) -> dict:
    """Accept the JSON a model returns, fenced or not."""
    text = payload.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def extract_with_llm(config: Config, text: str, today: date) -> ShipmentFacts:
    """Ask the configured model. Raises LlmUnavailable rather than guessing."""
    if not config.llm_api_key:
        raise LlmUnavailable("no API key configured")
    call = PROVIDERS.get(config.llm_provider)
    if call is None:
        raise LlmUnavailable(f"unknown provider {config.llm_provider!r}")

    prompt = PROMPT.replace("{today}", today.isoformat()).replace("{text}", text[:8_000])

    try:
        raw = call(config, prompt, LLM_TIMEOUT_SECONDS)
        data = parse_llm_json(raw)
    except Exception as err:  # noqa: BLE001 - any failure means "fall back"
        raise LlmUnavailable(str(err)) from err

    status = str(data.get("status") or STATUS_UNKNOWN)
    if status not in KNOWN_STATUSES:
        status = STATUS_UNKNOWN

    return ShipmentFacts(
        status=status,
        stops_remaining=_as_int(data.get("stops_remaining")),
        window_start=_as_time(data.get("window_start")),
        window_end=_as_time(data.get("window_end")),
        expected_date=_as_date(data.get("expected_date")),
        carrier=data.get("carrier") or None,
        source=SOURCE_LLM,
        confidence=str(data.get("confidence") or "low"),
    )


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_time(value: object):
    from datetime import time as _time

    if not isinstance(value, str) or ":" not in value:
        return None
    try:
        hour, minute = value.split(":")[:2]
        return _time(int(hour), int(minute))
    except ValueError:
        return None


def _as_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


def extract(page: Page, text: str, config: Config, today: date) -> ShipmentFacts:
    """CSS first, LLM only when the selectors no longer carry."""
    facts = extract_with_css(page, today)
    if facts.status != STATUS_UNKNOWN:
        _log_partial(facts)
        return facts

    LOGGER.warning("CSS selectors produced no status; trying the LLM fallback")
    try:
        llm_facts = extract_with_llm(config, text, today)
    except LlmUnavailable as err:
        LOGGER.warning("No LLM fallback available (%s); keeping the previous state", err)
        facts.source = SOURCE_NONE
        return facts

    # Keep the CSS field record: it is what says the selectors need attention.
    llm_facts.css_fields = facts.css_fields
    LOGGER.info("LLM fallback produced status=%s confidence=%s", llm_facts.status, llm_facts.confidence)
    return llm_facts


def _log_partial(facts: ShipmentFacts) -> None:
    missing = [name for name in TRACKED_FIELDS if not facts.css_fields.get(name)]
    if missing:
        # Before dispatch there is no tracking code and no carrier yet, so that
        # is expected rather than a sign the selectors slipped.
        expected = facts.status == STATUS_ORDERED and set(missing) <= {"tracking_code", "carrier"}
        log = LOGGER.debug if expected else LOGGER.info
        log("CSS extraction incomplete, missing: %s", ", ".join(missing))
    if facts.status == STATUS_OUT_FOR_DELIVERY and facts.stops_remaining is None:
        LOGGER.debug("Out for delivery but no stop count on the page")
    if facts.status == STATUS_DELIVERED:
        LOGGER.debug("Shipment reports delivered")
