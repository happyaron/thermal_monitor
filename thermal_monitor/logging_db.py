from __future__ import annotations
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from thermal_monitor.models import ThermalReading

log = logging.getLogger(__name__)


def _open_log_db(path: str) -> sqlite3.Connection:
    """
    Open (or create) the SQLite readings log at *path*.
    Creates the parent directory and the table if they don't exist yet.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            ts      TEXT NOT NULL,
            source  TEXT NOT NULL,
            sensor  TEXT NOT NULL,
            value   REAL NOT NULL,
            warn    REAL NOT NULL,
            crit    REAL NOT NULL,
            status  TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS readings_ts ON readings(ts)")
    conn.commit()
    return conn


def _write_log(
    conn: sqlite3.Connection,
    readings: List[ThermalReading],
    retention_days: int,
) -> None:
    """
    Insert current readings and prune rows older than *retention_days*.
    Runs inside a single transaction so a failure leaves the DB untouched.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        (ts, r.source, r.sensor, r.value, r.warn, r.crit, r.status)
        for r in readings
        if not r.error   # skip collection-error sentinels
    ]
    with conn:
        conn.executemany(
            "INSERT INTO readings VALUES (?,?,?,?,?,?,?)", rows
        )
        conn.execute(
            "DELETE FROM readings WHERE ts < datetime('now', ?)",
            (f"-{retention_days} days",),
        )
    log.debug("log: wrote %d row(s), retention %d days", len(rows), retention_days)
