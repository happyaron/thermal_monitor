"""Tests for SQLite reading log."""
from __future__ import annotations

import sqlite3

import pytest
from thermal_monitor.logging_db import _open_log_db, _write_log
from tests.conftest import make_reading


class TestOpenLogDb:
    def test_creates_table(self, tmp_path):
        conn = _open_log_db(str(tmp_path / "r.db"))
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cur.fetchall()]
        assert "readings" in tables
        conn.close()

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "readings.db")
        conn = _open_log_db(path)
        conn.close()
        assert (tmp_path / "nested" / "dir" / "readings.db").exists()

    def test_idempotent_on_existing_db(self, tmp_path):
        path = str(tmp_path / "r.db")
        conn = _open_log_db(path)
        conn.close()
        conn2 = _open_log_db(path)
        conn2.close()


class TestWriteLog:
    def _open(self, tmp_path):
        return _open_log_db(str(tmp_path / "r.db"))

    def test_inserts_readings(self, tmp_path):
        conn = self._open(tmp_path)
        readings = [make_reading(sensor=f"s{i}", value=float(20+i)) for i in range(3)]
        _write_log(conn, readings, retention_days=30)
        count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        assert count == 3
        conn.close()

    def test_skips_error_readings(self, tmp_path):
        conn = self._open(tmp_path)
        ok  = make_reading(sensor="ok", value=25.0)
        err = make_reading(sensor="err", value=0.0, error="fail")
        _write_log(conn, [ok, err], retention_days=30)
        count = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        assert count == 1
        conn.close()

    def test_retention_prunes_old_rows(self, tmp_path):
        conn = self._open(tmp_path)
        # Insert an old row directly
        conn.execute(
            "INSERT INTO readings VALUES (?,?,?,?,?,?,?)",
            ("2000-01-01T00:00:00Z", "src", "s", 25.0, 30.0, 45.0, "OK")
        )
        conn.commit()
        # Write with short retention — should prune the 2000-era row
        _write_log(conn, [make_reading()], retention_days=1)
        rows = conn.execute("SELECT ts FROM readings").fetchall()
        assert all("2000" not in row[0] for row in rows)
        conn.close()
