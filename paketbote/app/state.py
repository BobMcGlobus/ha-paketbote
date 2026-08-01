"""Persistent state, so a restart does not start from nothing.

Lives on the add-on config volume next to the browser profile. Small enough
that SQLite is overkill and exactly right at the same time: it gives atomic
writes without inventing a file format.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime, time
from pathlib import Path

from .models import SOURCE_AMAZON, STATE_IDLE, STATUS_UNKNOWN, Shipment
from .people import display_name, normalise_name, postcode_of

LOGGER = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("/config/state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS shipments (
    shipment_id     TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL,
    tracking_url    TEXT NOT NULL,
    title           TEXT,
    recipient       TEXT,
    delivery_address TEXT,
    carrier         TEXT,
    tracking_code   TEXT,
    source          TEXT,
    status          TEXT,
    stops_remaining INTEGER,
    window_start    TEXT,
    window_end      TEXT,
    expected_date   TEXT,
    state           TEXT,
    first_seen      TEXT,
    last_seen       TEXT,
    delivered_at    TEXT,
    missed          INTEGER NOT NULL DEFAULT 0,
    carrier_checked_at TEXT,
    items           TEXT
);

CREATE TABLE IF NOT EXISTS field_health (
    field      TEXT PRIMARY KEY,
    ok_count   INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_ok    TEXT,
    last_fail  TEXT
);

CREATE TABLE IF NOT EXISTS recipients (
    key      TEXT PRIMARY KEY,
    display  TEXT,
    hits     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recipient_postcodes (
    key      TEXT NOT NULL,
    postcode TEXT NOT NULL,
    hits     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key, postcode)
);

CREATE TABLE IF NOT EXISTS carrier_budget (
    day      TEXT NOT NULL,
    carrier  TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, carrier)
);

CREATE TABLE IF NOT EXISTS request_budget (
    day      TEXT PRIMARY KEY,
    requests INTEGER NOT NULL DEFAULT 0
);

-- Small odds and ends that belong to no other table: how far the mailbox has
-- been read, and which folder that count applies to.
CREATE TABLE IF NOT EXISTS notes (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _iso(value: date | time | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _as_time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _as_items(value: str | None) -> list[dict]:
    try:
        items = json.loads(value) if value else []
    except ValueError:
        return []
    return [i for i in items if isinstance(i, dict)]


def _as_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    """Everything that has to survive an add-on restart."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # The interface writes too, so allow concurrent readers and give
        # either side a moment rather than failing on a locked database.
        self._db = sqlite3.connect(str(self._path), isolation_level=None, timeout=10.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.executescript(SCHEMA)
        self._migrate()
        LOGGER.debug("State database at %s", self._path)

    def _migrate(self) -> None:
        """Add columns that later versions introduced.

        CREATE TABLE IF NOT EXISTS leaves an existing table untouched, so a
        database written by an older build needs the new columns added.
        """
        existing = {row["name"] for row in self._db.execute("PRAGMA table_info(shipments)")}
        for column, kind in (("recipient", "TEXT"), ("delivery_address", "TEXT"),
                             ("tracking_code", "TEXT"), ("source", "TEXT"),
                             ("delivered_at", "TEXT"), ("missed", "INTEGER DEFAULT 0"),
                             ("carrier_checked_at", "TEXT"), ("items", "TEXT")):
            if column not in existing:
                self._db.execute(f"ALTER TABLE shipments ADD COLUMN {column} {kind}")
                LOGGER.info("Added column %s to the shipments table", column)

    def close(self) -> None:
        self._db.close()

    # -- shipments ---------------------------------------------------------

    def save(self, shipment: Shipment) -> None:
        self._db.execute(
            """
            INSERT INTO shipments (shipment_id, order_id, tracking_url, title, recipient,
                                   delivery_address, carrier, tracking_code, source,
                                   status, stops_remaining, window_start, window_end,
                                   expected_date, state, delivered_at, missed, carrier_checked_at, items, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,COALESCE(
                (SELECT first_seen FROM shipments WHERE shipment_id = ?), ?), ?)
            ON CONFLICT(shipment_id) DO UPDATE SET
                order_id=excluded.order_id,
                tracking_url=excluded.tracking_url,
                title=excluded.title,
                recipient=excluded.recipient,
                delivery_address=excluded.delivery_address,
                carrier=excluded.carrier,
                tracking_code=excluded.tracking_code,
                source=excluded.source,
                status=excluded.status,
                stops_remaining=excluded.stops_remaining,
                window_start=excluded.window_start,
                window_end=excluded.window_end,
                expected_date=excluded.expected_date,
                state=excluded.state,
                delivered_at=excluded.delivered_at,
                missed=excluded.missed,
                carrier_checked_at=excluded.carrier_checked_at,
                items=excluded.items,
                last_seen=excluded.last_seen
            """,
            (
                shipment.shipment_id,
                shipment.order_id,
                shipment.tracking_url,
                shipment.title,
                shipment.recipient,
                shipment.delivery_address,
                shipment.carrier,
                shipment.tracking_code,
                shipment.source,
                shipment.status,
                shipment.stops_remaining,
                _iso(shipment.window_start),
                _iso(shipment.window_end),
                _iso(shipment.expected_date),
                shipment.state,
                _iso(shipment.delivered_at),
                shipment.missed,
                _iso(shipment.carrier_checked_at),
                json.dumps(shipment.items, ensure_ascii=False),
                shipment.shipment_id,
                _iso(shipment.last_seen or datetime.now()),
                _iso(shipment.last_seen or datetime.now()),
            ),
        )

    def all_shipments(self) -> list[Shipment]:
        rows = self._db.execute("SELECT * FROM shipments").fetchall()
        return [self._row_to_shipment(row) for row in rows]

    def forget(self, shipment_id: str) -> None:
        self._db.execute("DELETE FROM shipments WHERE shipment_id = ?", (shipment_id,))

    @staticmethod
    def _row_to_shipment(row: sqlite3.Row) -> Shipment:
        return Shipment(
            shipment_id=row["shipment_id"],
            order_id=row["order_id"],
            tracking_url=row["tracking_url"],
            title=row["title"] or "",
            recipient=row["recipient"] or "",
            delivery_address=row["delivery_address"] or "",
            carrier=row["carrier"],
            tracking_code=row["tracking_code"] or "",
            source=row["source"] or SOURCE_AMAZON,
            status=row["status"] or STATUS_UNKNOWN,
            stops_remaining=row["stops_remaining"],
            window_start=_as_time(row["window_start"]),
            window_end=_as_time(row["window_end"]),
            expected_date=_as_date(row["expected_date"]),
            state=row["state"] or STATE_IDLE,
            last_seen=_as_datetime(row["last_seen"]),
            delivered_at=_as_datetime(row["delivered_at"]),
            missed=int(row["missed"] or 0),
            carrier_checked_at=_as_datetime(row["carrier_checked_at"]),
            items=_as_items(row["items"]),
        )

    # -- who lives where ---------------------------------------------------

    def learn_recipient(self, recipient: str, address: str = "") -> None:
        """Remember a spelling and, if the address gives one, a postcode.

        This is what lets DHL be asked with the right postcode for a parcel
        whose own page never showed an address.
        """
        key = normalise_name(recipient)
        if not key:
            return

        row = self._db.execute("SELECT display FROM recipients WHERE key = ?", (key,)).fetchone()
        best = display_name([row["display"] if row else "", recipient])
        self._db.execute(
            """
            INSERT INTO recipients (key, display, hits) VALUES (?, ?, 1)
            ON CONFLICT(key) DO UPDATE SET display = ?, hits = hits + 1
            """,
            (key, best, best),
        )

        postcode = postcode_of(address)
        if postcode:
            self._db.execute(
                """
                INSERT INTO recipient_postcodes (key, postcode, hits) VALUES (?, ?, 1)
                ON CONFLICT(key, postcode) DO UPDATE SET hits = hits + 1
                """,
                (key, postcode),
            )

    def postcodes_for(self, recipient: str) -> list[str]:
        """Postcodes seen for this recipient, most frequent first."""
        key = normalise_name(recipient)
        if not key:
            return []
        rows = self._db.execute(
            "SELECT postcode FROM recipient_postcodes WHERE key = ? ORDER BY hits DESC, postcode",
            (key,),
        ).fetchall()
        return [row["postcode"] for row in rows]

    def note_postcode_worked(self, recipient: str, postcode: str) -> None:
        """Bump the postcode that actually answered, so it is tried first."""
        key = normalise_name(recipient)
        if not key or not postcode:
            return
        self._db.execute(
            """
            INSERT INTO recipient_postcodes (key, postcode, hits) VALUES (?, ?, 3)
            ON CONFLICT(key, postcode) DO UPDATE SET hits = hits + 3
            """,
            (key, postcode),
        )

    def known_recipients(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT key, display, hits FROM recipients ORDER BY hits DESC, display"
        ).fetchall()
        return [
            {
                "key": row["key"],
                "display": row["display"] or row["key"],
                "postcodes": self.postcodes_for(row["display"] or row["key"]),
            }
            for row in rows
        ]

    # -- selector health ---------------------------------------------------

    def note(self, key: str, value: str) -> None:
        """Remember one small thing between runs."""
        self._db.execute(
            "INSERT INTO notes (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self._db.commit()

    def noted(self, key: str, default: str = "") -> str:
        row = self._db.execute("SELECT value FROM notes WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def shipment_with_code(self, code: str) -> Shipment | None:
        """Whether this tracking number is already being followed."""
        if not code:
            return None
        row = self._db.execute(
            "SELECT * FROM shipments WHERE tracking_code = ? LIMIT 1", (code,)
        ).fetchone()
        return self._row_to_shipment(row) if row else None

    def record_fields(self, fields: dict[str, bool], when: datetime | None = None) -> None:
        """Remember, per field, whether the CSS selectors delivered."""
        stamp = _iso(when or datetime.now())
        for name, ok in fields.items():
            self._db.execute(
                """
                INSERT INTO field_health (field, ok_count, fail_count, last_ok, last_fail)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(field) DO UPDATE SET
                    ok_count   = ok_count   + ?,
                    fail_count = fail_count + ?,
                    last_ok    = CASE WHEN ? THEN ? ELSE last_ok END,
                    last_fail  = CASE WHEN ? THEN last_fail ELSE ? END
                """,
                (
                    name,
                    1 if ok else 0,
                    0 if ok else 1,
                    stamp if ok else None,
                    None if ok else stamp,
                    1 if ok else 0,
                    0 if ok else 1,
                    ok,
                    stamp,
                    ok,
                    stamp,
                ),
            )

    def field_health(self) -> dict[str, dict]:
        rows = self._db.execute("SELECT * FROM field_health").fetchall()
        health: dict[str, dict] = {}
        for row in rows:
            total = row["ok_count"] + row["fail_count"]
            health[row["field"]] = {
                "ok": row["ok_count"],
                "fail": row["fail_count"],
                "rate": round(row["ok_count"] / total, 3) if total else None,
                "last_ok": row["last_ok"],
                "last_fail": row["last_fail"],
            }
        return health

    # -- request budget ----------------------------------------------------

    def count_requests(self, amount: int = 1, today: date | None = None) -> int:
        day = _iso(today or date.today())
        self._db.execute(
            """
            INSERT INTO request_budget (day, requests) VALUES (?, ?)
            ON CONFLICT(day) DO UPDATE SET requests = requests + ?
            """,
            (day, amount, amount),
        )
        return self.requests_today(today)

    def count_carrier_request(self, carrier: str, today: date | None = None) -> int:
        """Carriers have their own quotas, separate from the Amazon budget."""
        day = _iso(today or date.today())
        self._db.execute(
            """
            INSERT INTO carrier_budget (day, carrier, requests) VALUES (?, ?, 1)
            ON CONFLICT(day, carrier) DO UPDATE SET requests = requests + 1
            """,
            (day, carrier),
        )
        return self.carrier_requests_today(carrier, today)

    def carrier_requests_today(self, carrier: str, today: date | None = None) -> int:
        day = _iso(today or date.today())
        row = self._db.execute(
            "SELECT requests FROM carrier_budget WHERE day = ? AND carrier = ?",
            (day, carrier),
        ).fetchone()
        return int(row["requests"]) if row else 0

    def requests_today(self, today: date | None = None) -> int:
        day = _iso(today or date.today())
        row = self._db.execute(
            "SELECT requests FROM request_budget WHERE day = ?", (day,)
        ).fetchone()
        return int(row["requests"]) if row else 0
