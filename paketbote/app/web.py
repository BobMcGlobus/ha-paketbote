"""The add-on's own interface, served behind Home Assistant's ingress.

Runs as its own process on purpose: reading the state database and the status
file the scheduler writes keeps the two apart, so a mistake in here cannot take
the polling loop down with it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from . import __version__
from .config import Config
from .models import STATE_DELIVERED
from .state import DEFAULT_DB_PATH, Store

LOGGER = logging.getLogger(__name__)

STATUS_PATH = Path("/config/status.json")
POLL_REQUEST_PATH = Path("/config/.poll-now")
UI_DIR = Path(__file__).parent / "ui"

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8099


def read_status() -> dict:
    """What the scheduler last reported. Absent until the first cycle."""
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def shipment_payload(shipment) -> dict:
    return {
        "shipment_id": shipment.shipment_id,
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
    }


def create_app(db_path: Path | str = DEFAULT_DB_PATH) -> Flask:
    app = Flask(__name__, static_folder=None)
    config = Config.load()

    @app.get("/")
    def index():
        return send_from_directory(UI_DIR, "index.html")

    @app.get("/api/state")
    def state():
        # A fresh connection per request: cheap for SQLite, and it keeps the
        # scheduler's own connection untouched.
        store = Store(db_path)
        try:
            shipments = store.all_shipments()
            health = store.field_health()
            used = store.requests_today()
            dhl_used = store.carrier_requests_today("dhl")
        finally:
            store.close()

        shipments.sort(
            key=lambda s: (s.expected_date is None, s.expected_date, s.title.lower())
        )
        active = [s for s in shipments if s.state != STATE_DELIVERED]

        return jsonify(
            {
                "version": __version__,
                "now": datetime.now().astimezone().isoformat(),
                "status": read_status(),
                "shipments": [shipment_payload(s) for s in shipments],
                "counts": {"total": len(shipments), "active": len(active)},
                "budget": {
                    "amazon_used": used,
                    "amazon_cap": config.daily_request_cap,
                    "dhl_used": dhl_used,
                },
                "selectors": health,
                "features": {
                    "dhl": bool(config.dhl_api_key),
                    "llm": bool(config.llm_api_key),
                    "developer_mode": config.developer_mode,
                },
            }
        )

    @app.post("/api/poll")
    def request_poll():
        """Ask the scheduler to run a cycle now rather than at its own pace."""
        try:
            POLL_REQUEST_PATH.write_text(datetime.now().isoformat(), encoding="utf-8")
        except OSError as err:
            return jsonify({"ok": False, "error": str(err)}), 500
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
