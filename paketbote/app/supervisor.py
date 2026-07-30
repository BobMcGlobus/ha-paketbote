"""Broker credentials from the Supervisor, never from the add-on options.

Declaring `services: mqtt:want` in config.yaml is what grants access to this
endpoint. Keeping the password out of the options means it never shows up in
a config backup or a screenshot of the add-on page.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import requests

LOGGER = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor/services/mqtt"
TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class MqttCredentials:
    host: str
    port: int
    username: str = ""
    password: str = ""
    ssl: bool = False


class SupervisorUnavailable(Exception):
    """No Supervisor, or no MQTT service published to this add-on."""


def mqtt_credentials() -> MqttCredentials:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise SupervisorUnavailable("SUPERVISOR_TOKEN is not set")

    try:
        response = requests.get(
            SUPERVISOR_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as err:  # noqa: BLE001 - any failure means "no broker"
        raise SupervisorUnavailable(f"cannot read the MQTT service: {err}") from err

    data = payload.get("data") or {}
    if not data.get("host"):
        raise SupervisorUnavailable(
            "the Supervisor published no MQTT broker — is the Mosquitto add-on running?"
        )

    LOGGER.info("Using the MQTT broker at %s:%s from the Supervisor", data["host"], data.get("port"))
    return MqttCredentials(
        host=str(data["host"]),
        port=int(data.get("port") or 1883),
        username=str(data.get("username") or ""),
        password=str(data.get("password") or ""),
        ssl=bool(data.get("ssl")),
    )
