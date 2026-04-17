"""Tests for alerting: send_alerts cooldown, state, dry-run."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from thermal_monitor.alerts import send_alerts, _load_state, _save_state
from tests.conftest import make_reading


def _alerting_cfg(**kw):
    return {"mode": "webhook", "alert_cooldown": 300,
            "mention_all_on_crit": True, **kw}


class TestSendAlerts:
    def test_no_triggered_nothing_sent(self, capsys):
        readings = [make_reading(value=25.0, warn=40.0, crit=55.0)]
        state = {}
        send_alerts(readings, _alerting_cfg(), state, time.time(), dry_run=True)
        out = capsys.readouterr().out
        assert out == ""

    def test_warn_triggers_dry_run_output(self, capsys):
        r = make_reading(source="srv", sensor="Inlet", value=45.0, warn=40.0, crit=55.0)
        state = {}
        send_alerts([r], _alerting_cfg(), state, time.time(), dry_run=True)
        out = capsys.readouterr().out
        assert "WARNING" in out or "WARN" in out or "45.0" in out

    def test_crit_triggers_dry_run_output(self, capsys):
        r = make_reading(source="srv", sensor="Inlet", value=60.0, warn=40.0, crit=55.0)
        state = {}
        send_alerts([r], _alerting_cfg(), state, time.time(), dry_run=True)
        out = capsys.readouterr().out
        assert "CRITICAL" in out or "CRIT" in out or "60.0" in out

    def test_cooldown_suppresses_repeated_alert(self, capsys):
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        now = time.time()
        # Use dict format so last_status is known (plain float → is_new=True → fires)
        state = {r.alert_key: {"ts": now - 10, "status": "CRIT"}}
        send_alerts([r], _alerting_cfg(alert_cooldown=300), state, now, dry_run=True)
        out = capsys.readouterr().out
        assert out == ""

    def test_cooldown_expired_allows_resend(self, capsys):
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        now = time.time()
        state = {r.alert_key: now - 400}  # alerted 400s ago, cooldown=300s
        send_alerts([r], _alerting_cfg(alert_cooldown=300), state, now, dry_run=True)
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_state_updated_after_alert(self):
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        state = {}
        now = time.time()
        send_alerts([r], _alerting_cfg(), state, now, dry_run=True)
        assert r.alert_key in state
        entry = state[r.alert_key]
        assert abs(entry["ts"] - now) < 1.0
        assert entry["status"] == "CRIT"

    def test_state_updated_even_in_dry_run(self):
        """Dry-run must update state to prevent terminal spam."""
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        state = {}
        send_alerts([r], _alerting_cfg(), state, time.time(), dry_run=True)
        assert r.alert_key in state

    def test_mention_all_on_crit_shown_in_dry_run(self, capsys):
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        state = {}
        send_alerts([r], _alerting_cfg(mention_all_on_crit=True), state,
                    time.time(), dry_run=True)
        out = capsys.readouterr().out
        assert "@all" in out or "all" in out.lower()

    def test_no_mention_all_when_disabled(self, capsys):
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        state = {}
        send_alerts([r], _alerting_cfg(mention_all_on_crit=False), state,
                    time.time(), dry_run=True)
        # No @all mention line expected
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert not any("@all" in line and "critical" in line.lower() for line in lines)


class TestCooldownOnFailure:
    """H1: a failed WeCom send must not advance the cooldown — otherwise
    the next 5 minutes of WARN/CRIT readings get silently dropped."""

    def test_successful_send_advances_cooldown(self):
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        state = {}
        now = time.time()
        # Patch the sender to succeed silently.
        fake_send = MagicMock()
        with patch("thermal_monitor.alerts._make_sender",
                   return_value=("webhook", fake_send)):
            send_alerts([r], _alerting_cfg(), state, now, dry_run=False)
        fake_send.assert_called_once()
        assert r.alert_key in state
        assert abs(state[r.alert_key]["ts"] - now) < 1.0

    def test_failed_send_does_not_advance_cooldown(self):
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        state = {}
        now = time.time()
        # Patch the sender to raise — simulates a network error or WeCom 5xx.
        fake_send = MagicMock(side_effect=RuntimeError("network down"))
        with patch("thermal_monitor.alerts._make_sender",
                   return_value=("webhook", fake_send)):
            send_alerts([r], _alerting_cfg(), state, now, dry_run=False)
        fake_send.assert_called_once()
        # Cooldown unchanged → next cycle will retry.
        assert r.alert_key not in state

    def test_failed_send_then_success_is_not_suppressed(self):
        """Transient failure, then recovery within cooldown window, should
        allow the recovery cycle to actually send (not be suppressed)."""
        r = make_reading(source="s", sensor="t", value=60.0, warn=40.0, crit=55.0)
        state = {}
        cfg = _alerting_cfg(alert_cooldown=300)

        # Cycle 1: send fails, cooldown not advanced.
        with patch("thermal_monitor.alerts._make_sender",
                   return_value=("webhook",
                                 MagicMock(side_effect=RuntimeError("timeout")))):
            send_alerts([r], cfg, state, time.time(), dry_run=False)
        assert state == {}

        # Cycle 2: 30s later (well inside the 300s cooldown window) the send
        # succeeds — the alert must NOT be suppressed.
        success_sender = MagicMock()
        with patch("thermal_monitor.alerts._make_sender",
                   return_value=("webhook", success_sender)):
            send_alerts([r], cfg, state, time.time() + 30, dry_run=False)
        success_sender.assert_called_once()
        assert r.alert_key in state


class TestLoadSaveState:
    def test_load_nonexistent_returns_empty(self, tmp_path):
        state = _load_state(str(tmp_path / "missing.json"))
        assert state == {}

    def test_save_and_reload(self, tmp_path):
        path = str(tmp_path / "state.json")
        _save_state(path, {"key": 1234.5})
        loaded = _load_state(path)
        assert loaded == {"key": 1234.5}

    def test_save_unwritable_path_does_not_raise(self):
        # Should log warning, not raise.
        _save_state("/nonexistent/path/state.json", {"k": 1})
