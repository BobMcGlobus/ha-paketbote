"""Settings the interface owns.

The add-on options in Home Assistant seed the first run. From then on this
file is the source of truth, so everything can be changed in the interface
without a trip through the Supervisor and a container restart.

The two exceptions are marked `restart`: screen size and log level are read by
the s6 services when they start, so changing them here does nothing until the
add-on is restarted. Saying so in the interface is better than pretending.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SETTINGS_PATH = Path("/config/settings.json")


@dataclass(frozen=True)
class Field:
    key: str
    kind: str  # int | bool | text | password | select
    group: str
    minimum: int | None = None
    maximum: int | None = None
    options: tuple[str, ...] = ()
    restart: bool = False
    unit: str = ""

    def coerce(self, value):
        """Bring a value from the interface into range, or reject it."""
        if self.kind == "bool":
            return bool(value)
        if self.kind == "int":
            number = int(value)
            if self.minimum is not None:
                number = max(self.minimum, number)
            if self.maximum is not None:
                number = min(self.maximum, number)
            return number
        text = str(value or "").strip()
        if self.kind == "select" and self.options and text not in self.options:
            raise ValueError(f"{self.key}: {text!r} is not one of {self.options}")
        return text


FIELDS: tuple[Field, ...] = (
    Field("amazon_domain", "text", "amazon"),
    Field("language", "select", "interface", options=("auto", "de", "en")),
    Field("display_width", "int", "interface", 640, 3840, restart=True, unit="px"),
    Field("display_height", "int", "interface", 480, 2160, restart=True, unit="px"),

    Field("poll_idle_minutes", "int", "polling", 5, 1440, unit="min"),
    Field("poll_pending_minutes", "int", "polling", 1, 240, unit="min"),
    Field("poll_window_minutes", "int", "polling", 1, 120, unit="min"),
    Field("poll_approaching_minutes", "int", "polling", 1, 60, unit="min"),
    Field("poll_imminent_minutes", "int", "polling", 1, 30, unit="min"),
    Field("approaching_stops_threshold", "int", "polling", 1, 50),
    Field("imminent_stops_threshold", "int", "polling", 1, 20),
    Field("quiet_hours_start", "int", "polling", 0, 23),
    Field("quiet_hours_end", "int", "polling", 0, 23),
    Field("daily_request_cap", "int", "polling", 50, 2000),
    Field("jitter_percent", "int", "polling", 0, 50, unit="%"),

    Field("dhl_api_key", "password", "carriers"),
    Field("dhl_poll_minutes", "int", "carriers", 5, 720, unit="min"),
    Field("ups_client_id", "password", "carriers"),
    Field("ups_client_secret", "password", "carriers"),
    Field("ups_poll_minutes", "int", "carriers", 5, 720, unit="min"),
    Field("fedex_client_id", "password", "carriers"),
    Field("fedex_client_secret", "password", "carriers"),
    Field("fedex_poll_minutes", "int", "carriers", 5, 720, unit="min"),
    Field("hermes_poll_minutes", "int", "carriers", 5, 720, unit="min"),
    Field("dpd_poll_minutes", "int", "carriers", 5, 720, unit="min"),
    Field("web_fallback", "bool", "carriers"),

    Field("llm_provider", "select", "extraction", options=("gemini", "openai", "anthropic")),
    Field("llm_model", "text", "extraction"),
    Field("llm_api_key", "password", "extraction"),

    Field("developer_mode", "bool", "advanced"),
    Field("dump_on_start", "bool", "advanced"),
    Field("log_level", "select", "advanced",
          options=("trace", "debug", "info", "warning", "error"), restart=True),
)

BY_KEY = {f.key: f for f in FIELDS}
GROUPS = ("amazon", "polling", "carriers", "extraction", "interface", "advanced")

# Not a Field: it is a list, edited by tapping a recipient rather than typing.
HIDDEN_RECIPIENTS = "hidden_recipients"


def load() -> dict:
    """Whatever the interface has saved. Empty on a fresh install."""
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(values: dict) -> dict:
    """Merge and store. Unknown keys are dropped rather than trusted."""
    current = load()
    for key, value in values.items():
        if key == HIDDEN_RECIPIENTS:
            current[key] = [str(v) for v in value or []]
            continue
        field_spec = BY_KEY.get(key)
        if field_spec is None:
            LOGGER.debug("Ignoring unknown setting %s", key)
            continue
        # An empty password field means "leave it alone". Without this the
        # settings page silently wipes a key that was set elsewhere, and the
        # carrier stops being asked with no visible reason.
        if field_spec.kind == "password" and not str(value or "").strip():
            continue

        try:
            current[key] = field_spec.coerce(value)
        except (TypeError, ValueError) as err:
            LOGGER.warning("Rejecting %s: %s", key, err)

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def schema() -> list[dict]:
    """What the interface needs to render the settings page."""
    return [
        {
            "key": f.key,
            "kind": f.kind,
            "group": f.group,
            "min": f.minimum,
            "max": f.maximum,
            "options": list(f.options),
            "restart": f.restart,
            "unit": f.unit,
        }
        for f in FIELDS
    ]
