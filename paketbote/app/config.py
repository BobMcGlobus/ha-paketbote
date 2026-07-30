"""Add-on options as a typed object.

The Supervisor renders the user's options into /data/options.json before the
container starts, so nothing here has to talk to the Supervisor API.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_OPTIONS_PATH = Path("/data/options.json")

# Maps the add-on's log_level option onto Python's logging levels.
LOG_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


@dataclass(frozen=True)
class Config:
    """Every add-on option, with the same defaults as config.yaml."""

    amazon_domain: str = "amazon.de"
    display_width: int = 1280
    display_height: int = 1024
    llm_provider: str = "gemini"
    llm_model: str = "gemini-2.5-flash"
    llm_api_key: str = ""
    poll_idle_minutes: int = 60
    poll_pending_minutes: int = 15
    poll_window_minutes: int = 10
    poll_approaching_minutes: int = 3
    poll_imminent_minutes: int = 1
    approaching_stops_threshold: int = 7
    imminent_stops_threshold: int = 2
    daily_request_cap: int = 300
    jitter_percent: int = 20
    quiet_hours_start: int = 22
    quiet_hours_end: int = 6
    dump_on_start: bool = False
    log_level: str = "info"

    @property
    def base_url(self) -> str:
        return f"https://www.{self.amazon_domain}"

    @property
    def order_history_url(self) -> str:
        return f"{self.base_url}/gp/css/order-history"

    @property
    def python_log_level(self) -> int:
        return LOG_LEVELS.get(self.log_level.lower(), logging.INFO)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """Read the options file, ignoring anything this build does not know."""
        if path is None:
            path = Path(os.environ.get("PAKETBOTE_OPTIONS", DEFAULT_OPTIONS_PATH))

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            LOGGER.debug("No options file at %s; using defaults", path)
            return cls()
        except json.JSONDecodeError as err:
            LOGGER.warning("Options file at %s is not valid JSON (%s); using defaults", path, err)
            return cls()

        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            LOGGER.debug("Ignoring unknown options: %s", ", ".join(sorted(unknown)))

        return cls(**{k: v for k, v in raw.items() if k in known})
