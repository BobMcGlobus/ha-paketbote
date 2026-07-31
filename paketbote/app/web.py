"""The add-on's own interface, served behind Home Assistant's ingress.

Runs as its own process on purpose: reading the state database and the status
file the scheduler writes keeps the two apart, so a mistake in here cannot take
the polling loop down with it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import __version__
from . import settings as settings_module
from .carriers import registry
from .carriers.base import CarrierError, NotFound
from .carriers import trackers as carrier_trackers
from .config import Config
from .people import normalise_name
from .models import (
    SOURCE_MANUAL,
    STATE_DELIVERED,
    STATE_IDLE,
    STATUS_DELIVERED,
    STATUS_UNKNOWN,
    Shipment,
    sanitise_id,
    shorten,
)
from .state import DEFAULT_DB_PATH, Store

LOGGER = logging.getLogger(__name__)

STATUS_PATH = Path("/config/status.json")
POLL_REQUEST_PATH = Path("/config/.poll-now")
UI_DIR = Path(__file__).parent / "ui"

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8099

# Mirrors the scheduler: delivered parcels stay in view this long.
KEEP_DELIVERED_DAYS = 3


def read_status() -> dict:
    """What the scheduler last reported. Absent until the first cycle."""
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def shipment_payload(shipment) -> dict:
    return {
        "shipment_id": shipment.shipment_id,
        "source": shipment.source,
        "order_id": shipment.order_id,
        "title": shipment.title,
        "recipient": shipment.recipient,
        "delivery_address": shipment.delivery_address,
        "carrier": shipment.carrier,
        "tracking_code": shipment.tracking_code,
        "status": shipment.status,
        "state": shipment.state,
        "stops_remaining": shipment.stops_remaining,
        "window_start": shipment.window_start.isoformat() if shipment.window_start else None,
        "window_end": shipment.window_end.isoformat() if shipment.window_end else None,
        "expected_date": shipment.expected_date.isoformat() if shipment.expected_date else None,
        "tracking_url": shipment.tracking_url,
        "last_seen": shipment.last_seen.isoformat() if shipment.last_seen else None,
        "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None,
        "bucket": bucket_of(shipment),
        "recipient_key": normalise_name(shipment.recipient),
        "items": shipment.items,
    }


def bucket_of(shipment) -> str:
    """Which section of the interface this parcel belongs in."""
    delivered = shipment.state == STATE_DELIVERED or shipment.status == STATUS_DELIVERED
    if not delivered and shipment.delivered_at is None:
        return "current"

    # Rows written before delivered_at existed carry no timestamp; fall back to
    # when they were last seen, and archive them if even that is missing.
    stamp = shipment.delivered_at or shipment.last_seen
    if stamp is None or datetime.now() - stamp > timedelta(days=KEEP_DELIVERED_DAYS):
        return "archive"
    return "delivered"


def _request_poll() -> bool:
    """Nudge the scheduler. It checks for this file while it sleeps."""
    try:
        POLL_REQUEST_PATH.write_text(datetime.now().isoformat(), encoding="utf-8")
        return True
    except OSError:
        LOGGER.warning("Could not write the poll request file")
        return False


def create_app(db_path: Path | str = DEFAULT_DB_PATH) -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(UI_DIR, "index.html")

    @app.get("/api/state")
    def state():
        # Read per request: settings change while this process is running.
        config = Config.load()
        # A fresh connection per request: cheap for SQLite, and it keeps the
        # scheduler's own connection untouched.
        store = Store(db_path)
        try:
            shipments = store.all_shipments()
            health = store.field_health()
            used = store.requests_today()
            carrier_used = {
                key: store.carrier_requests_today(key)
                for key in carrier_trackers.MODULES
            }
            recipients = store.known_recipients()
        finally:
            store.close()

        shipments.sort(
            key=lambda s: (s.expected_date is None, s.expected_date, s.title.lower())
        )
        buckets = [bucket_of(s) for s in shipments]
        active = [s for s, b in zip(shipments, buckets) if b == "current"]

        return jsonify(
            {
                "version": __version__,
                "now": datetime.now().astimezone().isoformat(),
                "status": read_status(),
                "shipments": [shipment_payload(s) for s in shipments],
                "counts": {
                    "total": len(shipments),
                    "active": len(active),
                    "delivered": buckets.count("delivered"),
                    "archive": buckets.count("archive"),
                },
                "budget": {
                    "amazon_used": used,
                    "amazon_cap": config.daily_request_cap,
                    "dhl_used": carrier_used["dhl"],
                    "carrier_used": carrier_used,
                },
                "selectors": health,
                "features": {
                    "dhl": bool(config.dhl_api_key),
                    "ups": bool(config.ups_client_id and config.ups_client_secret),
                    "fedex": bool(config.fedex_client_id and config.fedex_client_secret),
                    "llm": bool(config.llm_api_key),
                    "developer_mode": config.developer_mode,
                },
                "language": config.language,
                "carriers": registry.choices(),
                "recipients": recipients,
                "hidden_recipients": list(config.hidden_recipients),
            }
        )

    @app.get("/api/settings")
    def read_settings():
        current = Config.load()
        values = {}
        secrets = {}
        for spec in settings_module.schema():
            value = getattr(current, spec["key"], None)
            if spec["kind"] == "password":
                # Never hand a stored key back out; an empty box means unchanged.
                secrets[spec["key"]] = bool(value)
                value = ""
            values[spec["key"]] = value
        return jsonify({
            "secrets": secrets,
            "schema": settings_module.schema(),
            "groups": list(settings_module.GROUPS),
            "values": values,
        })

    @app.post("/api/test/<carrier>")
    def test_carrier(carrier):
        """Say plainly whether a carrier accepts the configured credentials."""
        if carrier not in carrier_trackers.MODULES:
            return jsonify({"ok": False, "reason": "unknown carrier"}), 404

        config = Config.load()
        store = Store(db_path)
        try:
            tracker = carrier_trackers.build(config, store)[carrier]
            if not tracker.available:
                return jsonify({"ok": False, "reason": "no_key"})
            ok, detail = tracker.probe()
        finally:
            store.close()
        LOGGER.info("%s credential test: %s (%s)", tracker.name,
                    "ok" if ok else "failed", detail)
        return jsonify({"ok": ok, "reason": detail})

    @app.post("/api/settings/reset")
    def reset_settings():
        """Back to the defaults, keeping the keys and the recipient filter."""
        current = settings_module.load()
        keep = {
            key: value for key, value in current.items()
            if key == settings_module.HIDDEN_RECIPIENTS
            or (settings_module.BY_KEY.get(key) and settings_module.BY_KEY[key].kind == "password")
        }
        settings_module.SETTINGS_PATH.write_text(
            json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Settings reset to defaults")
        return jsonify({"ok": True})

    @app.post("/api/settings")
    def write_settings():
        data = request.get_json(silent=True) or {}
        settings_module.save(data)
        LOGGER.info("Settings updated from the interface")
        return jsonify({"ok": True})

    @app.post("/api/shipments")
    def add_shipment():
        """Track a parcel the sources never saw — a friend's gift, a return."""
        data = request.get_json(silent=True) or {}
        code = str(data.get("tracking_code") or "").strip()
        carrier = str(data.get("carrier") or "").strip()

        if not code:
            return jsonify({"ok": False, "error": "tracking_code_required"}), 400
        info = registry.lookup(carrier)
        if info is None:
            return jsonify({"ok": False, "error": "unknown_carrier"}), 400

        shipment_id = f"manual-{sanitise_id(code)}"
        shipment = Shipment(
            shipment_id=shipment_id,
            order_id="",
            tracking_url=info.url_for(code),
            title=shorten(str(data.get("title") or "").strip()) or code,
            recipient=str(data.get("recipient") or "").strip(),
            carrier=info.name,
            tracking_code=code,
            source=SOURCE_MANUAL,
            last_seen=datetime.now(),
        )

        store = Store(db_path)
        try:
            store.save(shipment)
        finally:
            store.close()

        LOGGER.info("Added manual shipment %s (%s)", shipment_id, info.name)
        _request_poll()
        return jsonify({"ok": True, "shipment_id": shipment_id}), 201

    @app.patch("/api/shipments/<shipment_id>")
    def edit_shipment(shipment_id: str):
        """Only label and recipient: everything else comes from a carrier."""
        data = request.get_json(silent=True) or {}

        store = Store(db_path)
        try:
            found = {s.shipment_id: s for s in store.all_shipments()}.get(shipment_id)
            if found is None:
                return jsonify({"ok": False, "error": "not_found"}), 404
            if found.source != SOURCE_MANUAL:
                return jsonify({"ok": False, "error": "not_editable"}), 400

            if "title" in data:
                found.title = shorten(str(data["title"] or "").strip()) or found.tracking_code
            if "recipient" in data:
                found.recipient = str(data["recipient"] or "").strip()
            store.save(found)
        finally:
            store.close()

        LOGGER.info("Updated shipment %s", shipment_id)
        return jsonify({"ok": True})

    @app.post("/api/shipments/<shipment_id>/restore")
    def restore_shipment(shipment_id: str):
        """Put a wrongly archived parcel back into circulation.

        Clearing the delivery stamp is enough: the next poll works the state
        out again from what the source and the carrier say.
        """
        store = Store(db_path)
        try:
            found = {s.shipment_id: s for s in store.all_shipments()}.get(shipment_id)
            if found is None:
                return jsonify({"ok": False, "error": "not_found"}), 404
            found.delivered_at = None
            found.missed = 0
            found.status = STATUS_UNKNOWN
            found.state = STATE_IDLE
            store.save(found)
        finally:
            store.close()

        LOGGER.info("Restored %s from the archive", shipment_id)
        _request_poll()
        return jsonify({"ok": True})

    @app.delete("/api/shipments/<shipment_id>")
    def remove_shipment(shipment_id: str):
        store = Store(db_path)
        try:
            store.forget(shipment_id)
        finally:
            store.close()
        LOGGER.info("Removed shipment %s", shipment_id)
        _request_poll()
        return jsonify({"ok": True})

    @app.post("/api/poll")
    def request_poll():
        """Ask the scheduler to run a cycle now rather than at its own pace."""
        if not _request_poll():
            return jsonify({"ok": False, "error": "cannot_write_request"}), 500
        LOGGER.info("Poll requested from the interface")
        return jsonify({"ok": True})

    return app


def main() -> int:
    config = Config.load()
    logging.basicConfig(
        level=config.python_log_level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    from waitress import serve

    LOGGER.info("Interface listening on %s:%s", LISTEN_HOST, LISTEN_PORT)
    serve(create_app(), host=LISTEN_HOST, port=LISTEN_PORT, threads=4, _quiet=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
