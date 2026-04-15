"""Tests for log-line output (service mode)."""
from __future__ import annotations

import logging

import pytest
from thermal_monitor.display_log import (
    SystemdFormatter,
    SD_PRIORITY,
    configure_log_output,
    emit_status_log,
)
from tests.conftest import make_reading


# ── SystemdFormatter ──────────────────────────────────────────────────────────

class TestSystemdFormatter:
    def _record(self, level: int, msg: str = "hello") -> logging.LogRecord:
        return logging.LogRecord(
            name="x", level=level, pathname=__file__, lineno=1,
            msg=msg, args=None, exc_info=None,
        )

    def test_info_prefixed_with_priority_6(self):
        fmt = SystemdFormatter("%(message)s")
        out = fmt.format(self._record(logging.INFO, "heartbeat"))
        assert out == "<6>heartbeat"

    def test_warning_prefixed_with_priority_4(self):
        fmt = SystemdFormatter("%(message)s")
        out = fmt.format(self._record(logging.WARNING, "hot"))
        assert out == "<4>hot"

    def test_critical_prefixed_with_priority_2(self):
        fmt = SystemdFormatter("%(message)s")
        out = fmt.format(self._record(logging.CRITICAL, "on fire"))
        assert out == "<2>on fire"

    def test_error_prefixed_with_priority_3(self):
        fmt = SystemdFormatter("%(message)s")
        out = fmt.format(self._record(logging.ERROR, "unreachable"))
        assert out == "<3>unreachable"

    def test_priority_map_covers_standard_levels(self):
        for lvl in (logging.DEBUG, logging.INFO, logging.WARNING,
                    logging.ERROR, logging.CRITICAL):
            assert lvl in SD_PRIORITY


# ── configure_log_output ──────────────────────────────────────────────────────

class TestConfigureLogOutput:
    def _restore_root(self):
        # Pytest's caplog uses the root logger; tests here reach into it and
        # need to leave it tidy for downstream tests.
        root = logging.getLogger()
        root.handlers[:] = []
        root.setLevel(logging.WARNING)

    def test_systemd_handler_uses_systemd_formatter(self):
        try:
            configure_log_output("systemd")
            root = logging.getLogger()
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0].formatter, SystemdFormatter)
        finally:
            self._restore_root()

    def test_plain_handler_uses_plain_formatter(self):
        try:
            configure_log_output("plain")
            root = logging.getLogger()
            assert len(root.handlers) == 1
            assert not isinstance(root.handlers[0].formatter, SystemdFormatter)
        finally:
            self._restore_root()

    def test_debug_param_sets_debug_level(self):
        try:
            configure_log_output("plain", debug=True)
            assert logging.getLogger().level == logging.DEBUG
        finally:
            self._restore_root()

    def test_default_info_level(self):
        try:
            configure_log_output("plain")
            assert logging.getLogger().level == logging.INFO
        finally:
            self._restore_root()


# ── emit_status_log ───────────────────────────────────────────────────────────

class TestEmitStatusLog:
    def test_heartbeat_emitted_for_all_ok(self, caplog):
        readings = [make_reading(source="s", sensor="Inlet", value=25.0)]
        with caplog.at_level(logging.DEBUG, logger="thermal_monitor.status"):
            emit_status_log(readings)
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1
        assert "ok=1" in infos[0].getMessage()
        assert "warn=0" in infos[0].getMessage()

    def test_warn_reading_emits_warning(self, caplog):
        readings = [make_reading(source="s", sensor="Inlet",
                                 value=42.0, warn=40.0, crit=55.0)]
        with caplog.at_level(logging.DEBUG, logger="thermal_monitor.status"):
            emit_status_log(readings)
        warns = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warns) == 1
        assert "42.0" in warns[0].getMessage()
        assert "s" in warns[0].getMessage()
        assert "Inlet" in warns[0].getMessage()

    def test_crit_reading_emits_critical(self, caplog):
        readings = [make_reading(source="s", sensor="Inlet",
                                 value=60.0, warn=40.0, crit=55.0)]
        with caplog.at_level(logging.DEBUG, logger="thermal_monitor.status"):
            emit_status_log(readings)
        crits = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(crits) == 1
        assert "60.0" in crits[0].getMessage()

    def test_error_reading_emits_error(self, caplog):
        readings = [make_reading(source="dead", sensor="(source)",
                                 value=0.0, error="unreachable")]
        with caplog.at_level(logging.DEBUG, logger="thermal_monitor.status"):
            emit_status_log(readings)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "unreachable" in errors[0].getMessage()

    def test_ok_readings_do_not_emit_per_sensor_lines(self, caplog):
        readings = [
            make_reading(source="s", sensor="a", value=20.0),
            make_reading(source="s", sensor="b", value=22.0),
        ]
        with caplog.at_level(logging.DEBUG, logger="thermal_monitor.status"):
            emit_status_log(readings)
        # Only the heartbeat — no WARN/ERROR/CRIT lines for OK readings.
        non_info = [r for r in caplog.records if r.levelno != logging.INFO]
        assert non_info == []

    def test_mixed_readings_count_summary(self, caplog, mixed_readings):
        with caplog.at_level(logging.DEBUG, logger="thermal_monitor.status"):
            emit_status_log(mixed_readings)
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1
        msg = infos[0].getMessage()
        # mixed_readings fixture: 2 OK, 1 WARN, 1 ERROR, 0 CRIT across 3 sources
        assert "ok=2" in msg
        assert "warn=1" in msg
        assert "err=1" in msg
        assert "sources=3" in msg
