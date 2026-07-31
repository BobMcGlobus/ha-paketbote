"""MQTT Discovery: the add-on's only contact with Home Assistant.

Aggregates are computed here and published as ready-made entities. The plan is
explicit about not rebuilding them as template sensors in HA: a Jinja
aggregation over a changing list of entities breaks the moment a new shipment
shows up, and automations would break with it.
"""

from __future__ import annotations

import json
import logging

import paho.mqtt.client as mqtt

from .models import Shipment
from .supervisor import MqttCredentials

LOGGER = logging.getLogger(__name__)

BASE_TOPIC = "paketbote"
AVAILABILITY_TOPIC = f"{BASE_TOPIC}/status"
SUMMARY_TOPIC = f"{BASE_TOPIC}/summary/state"
HEALTH_TOPIC = f"{BASE_TOPIC}/health/state"
SHIPMENTS_TOPIC = f"{BASE_TOPIC}/shipments/state"
DISCOVERY_PREFIX = "homeassistant"

PAYLOAD_ONLINE = "online"
PAYLOAD_OFFLINE = "offline"

HUB_DEVICE = {
    "identifiers": [BASE_TOPIC],
    "name": "Paketbote",
    "manufacturer": "BobMcGlobus",
    "model": "Amazon-Lieferverfolgung",
}


def _boolean(field: str) -> str:
    return f"{{{{ 'ON' if value_json.{field} else 'OFF' }}}}"


def _plain(field: str) -> str:
    return f"{{{{ value_json.{field} }}}}"


# Stable entity ids: these are what automations reference, and they exist
# whether or not anything is currently in transit.
AGGREGATE_ENTITIES: list[dict] = [
    {
        "key": "pakete_heute",
        "component": "sensor",
        "name": "Pakete heute",
        "icon": "mdi:package-variant-closed",
        "state_class": "measurement",
    },
    {
        "key": "pakete_aktiv",
        "component": "sensor",
        "name": "Pakete aktiv",
        "icon": "mdi:package-variant",
        "state_class": "measurement",
    },
    {
        "key": "naechste_stopps",
        "component": "sensor",
        "name": "Nächste Stopps",
        "icon": "mdi:map-marker-distance",
        "state_class": "measurement",
    },
    {
        "key": "gesamtstatus",
        "component": "sensor",
        "name": "Gesamtstatus",
        "icon": "mdi:progress-clock",
    },
    {
        "key": "naechstes_fenster",
        "component": "sensor",
        "name": "Nächstes Fenster",
        "device_class": "timestamp",
    },
    {
        "key": "letzter_abruf",
        "component": "sensor",
        "name": "Letzter Abruf",
        "device_class": "timestamp",
    },
    {
        "key": "extraktionsmethode",
        "component": "sensor",
        "name": "Extraktionsmethode",
        "icon": "mdi:code-tags",
    },
    {
        # One entity carrying every shipment as an attribute. A Lovelace card
        # renders that far more easily than a changing set of devices.
        "key": "sendungen",
        "component": "sensor",
        "name": "Sendungen",
        "icon": "mdi:package-variant-closed",
        "state_class": "measurement",
        "state_topic": SHIPMENTS_TOPIC,
        "value_key": "count",
        "attributes_topic": SHIPMENTS_TOPIC,
    },
    {
        "key": "zustellfenster_aktiv",
        "component": "binary_sensor",
        "name": "Zustellfenster aktiv",
        "icon": "mdi:truck-delivery",
    },
    {
        "key": "zustellung_unmittelbar",
        "component": "binary_sensor",
        "name": "Zustellung unmittelbar",
        "icon": "mdi:truck-fast",
    },
    {
        "key": "login_erforderlich",
        "component": "binary_sensor",
        "name": "Login erforderlich",
        "device_class": "problem",
    },
    {
        "key": "gedrosselt",
        "component": "binary_sensor",
        "name": "Gedrosselt",
        "device_class": "problem",
    },
    {
        "key": "selektoren_defekt",
        "component": "binary_sensor",
        "name": "Selektoren defekt",
        "device_class": "problem",
        "attributes_topic": HEALTH_TOPIC,
    },
]

# Per shipment. These come and go; automations should not depend on them.
SHIPMENT_ENTITIES: list[dict] = [
    {"key": "status", "component": "sensor", "name": "Status", "icon": "mdi:progress-clock"},
    {
        "key": "stops_remaining",
        "component": "sensor",
        "name": "Stopps",
        "icon": "mdi:map-marker-distance",
        "state_class": "measurement",
    },
    {"key": "window_start", "component": "sensor", "name": "Fenster ab", "icon": "mdi:clock-start"},
    {"key": "expected_date", "component": "sensor", "name": "Erwartet", "icon": "mdi:calendar"},
    {"key": "title", "component": "sensor", "name": "Titel", "icon": "mdi:tag-text"},
    {"key": "carrier", "component": "sensor", "name": "Zusteller", "icon": "mdi:truck"},
    {"key": "recipient", "component": "sensor", "name": "Empfänger", "icon": "mdi:account"},
    {
        "key": "delivery_address",
        "component": "sensor",
        "name": "Lieferadresse",
        "icon": "mdi:map-marker",
    },
]


class Publisher:
    """A thin wrapper: connect, announce, publish, retire."""

    def __init__(self, credentials: MqttCredentials, version: str) -> None:
        self._credentials = credentials
        self._version = version
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=BASE_TOPIC)
        self._announced: set[str] = set()

    @property
    def announced(self) -> set[str]:
        """Shipments Home Assistant currently knows about."""
        return set(self._announced)

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        creds = self._credentials
        if creds.username:
            self._client.username_pw_set(creds.username, creds.password)
        if creds.ssl:
            self._client.tls_set()

        # Last will, so entities go unavailable rather than stale when the
        # add-on dies. The plan asks for unavailable, never unknown.
        self._client.will_set(AVAILABILITY_TOPIC, PAYLOAD_OFFLINE, retain=True)
        self._client.connect(creds.host, creds.port, keepalive=60)
        self._client.loop_start()
        self._client.publish(AVAILABILITY_TOPIC, PAYLOAD_ONLINE, retain=True)
        LOGGER.info("Connected to the MQTT broker at %s:%s", creds.host, creds.port)

    def disconnect(self) -> None:
        try:
            self._client.publish(AVAILABILITY_TOPIC, PAYLOAD_OFFLINE, retain=True)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - shutdown must not raise
            LOGGER.debug("MQTT did not disconnect cleanly", exc_info=True)

    # -- discovery ---------------------------------------------------------

    def _device(self, extra: dict | None = None) -> dict:
        device = dict(HUB_DEVICE)
        device["sw_version"] = self._version
        if extra:
            device.update(extra)
        return device

    def _discovery_topic(self, component: str, object_id: str) -> str:
        return f"{DISCOVERY_PREFIX}/{component}/{BASE_TOPIC}/{object_id}/config"

    def announce_aggregates(self) -> None:
        for entity in AGGREGATE_ENTITIES:
            object_id = f"{BASE_TOPIC}_{entity['key']}"
            payload = {
                "name": entity["name"],
                "unique_id": object_id,
                "object_id": object_id,
                "state_topic": entity.get("state_topic", SUMMARY_TOPIC),
                "availability_topic": AVAILABILITY_TOPIC,
                "payload_available": PAYLOAD_ONLINE,
                "payload_not_available": PAYLOAD_OFFLINE,
                "device": self._device(),
            }
            if entity["component"] == "binary_sensor":
                payload["value_template"] = _boolean(entity["key"])
                payload["payload_on"] = "ON"
                payload["payload_off"] = "OFF"
            else:
                payload["value_template"] = _plain(entity.get("value_key", entity["key"]))
            for optional in ("icon", "device_class", "state_class"):
                if optional in entity:
                    payload[optional] = entity[optional]
            if "attributes_topic" in entity:
                payload["json_attributes_topic"] = entity["attributes_topic"]

            self._publish(self._discovery_topic(entity["component"], object_id), payload, retain=True)
        LOGGER.info("Announced %d aggregate entities", len(AGGREGATE_ENTITIES))

    def announce_shipment(self, shipment: Shipment) -> None:
        if shipment.shipment_id in self._announced:
            return
        device = self._device(
            {
                "identifiers": [f"{BASE_TOPIC}_{shipment.shipment_id}"],
                "name": f"Paket {shipment.title or shipment.shipment_id}",
                "model": shipment.carrier or "Sendung",
                "manufacturer": "Amazon",
                "via_device": BASE_TOPIC,
            }
        )
        for entity in SHIPMENT_ENTITIES:
            object_id = f"{BASE_TOPIC}_{shipment.shipment_id}_{entity['key']}"
            payload = {
                "name": entity["name"],
                "unique_id": object_id,
                "object_id": object_id,
                "state_topic": self._shipment_topic(shipment.shipment_id),
                "availability_topic": AVAILABILITY_TOPIC,
                "payload_available": PAYLOAD_ONLINE,
                "payload_not_available": PAYLOAD_OFFLINE,
                "value_template": _plain(entity["key"]),
                "device": device,
            }
            for optional in ("icon", "device_class", "state_class"):
                if optional in entity:
                    payload[optional] = entity[optional]
            self._publish(self._discovery_topic(entity["component"], object_id), payload, retain=True)

        self._announced.add(shipment.shipment_id)
        LOGGER.info("Announced shipment %s (%s)", shipment.shipment_id, shipment.title or "untitled")

    def retire_shipment(self, shipment_id: str) -> None:
        """Remove a device from HA by publishing an empty retained payload."""
        for entity in SHIPMENT_ENTITIES:
            object_id = f"{BASE_TOPIC}_{shipment_id}_{entity['key']}"
            self._client.publish(
                self._discovery_topic(entity["component"], object_id), b"", retain=True
            )
        self._client.publish(self._shipment_topic(shipment_id), b"", retain=True)
        self._announced.discard(shipment_id)
        LOGGER.info("Retired shipment %s", shipment_id)

    # -- state -------------------------------------------------------------

    @staticmethod
    def _shipment_topic(shipment_id: str) -> str:
        return f"{BASE_TOPIC}/shipment/{shipment_id}/state"

    def publish_summary(self, summary: dict) -> None:
        self._publish(SUMMARY_TOPIC, summary, retain=True)

    def publish_shipments(self, payload: dict) -> None:
        self._publish(SHIPMENTS_TOPIC, payload, retain=True)

    def publish_health(self, health: dict) -> None:
        self._publish(HEALTH_TOPIC, health, retain=True)

    def publish_shipment(self, shipment: Shipment) -> None:
        payload = {
            "shipment_id": shipment.shipment_id,
            "order_id": shipment.order_id,
            "title": shipment.title,
            "recipient": shipment.recipient,
            "delivery_address": shipment.delivery_address,
            "status": shipment.status,
            "state": shipment.state,
            "carrier": shipment.carrier,
            "stops_remaining": shipment.stops_remaining,
            "window_start": shipment.window_start.isoformat() if shipment.window_start else None,
            "window_end": shipment.window_end.isoformat() if shipment.window_end else None,
            "expected_date": shipment.expected_date.isoformat() if shipment.expected_date else None,
            "tracking_url": shipment.tracking_url,
        }
        self._publish(self._shipment_topic(shipment.shipment_id), payload, retain=True)

    def _publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload, ensure_ascii=False), retain=retain)
        LOGGER.debug("Published to %s", topic)
