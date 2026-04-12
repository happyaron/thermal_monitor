"""Tests for pure-logic analysis helpers: most_urgent, primary_inlet, alert_hint."""
from __future__ import annotations

import pytest
from thermal_monitor.analysis import most_urgent, primary_inlet, alert_hint, _abbrev_name
from tests.conftest import make_reading


# ── most_urgent ────────────────────────────────────────────────────────────────

class TestMostUrgent:
    def test_single_ok(self):
        r = make_reading(value=25.0, warn=40.0, crit=55.0)
        assert most_urgent([r]) is r

    def test_crit_beats_warn(self):
        w = make_reading(sensor="w", value=42.0, warn=40.0, crit=55.0)
        c = make_reading(sensor="c", value=60.0, warn=40.0, crit=55.0)
        assert most_urgent([w, c]) is c

    def test_same_tier_smallest_headroom_wins(self):
        r1 = make_reading(sensor="r1", value=38.0, warn=40.0, crit=55.0)  # headroom 2
        r2 = make_reading(sensor="r2", value=39.5, warn=40.0, crit=55.0)  # headroom 0.5
        assert most_urgent([r1, r2]) is r2

    def test_crit_past_threshold_negative_headroom(self):
        c1 = make_reading(sensor="c1", value=60.0, warn=40.0, crit=55.0)  # headroom -5
        c2 = make_reading(sensor="c2", value=70.0, warn=40.0, crit=55.0)  # headroom -15
        assert most_urgent([c1, c2]) is c2

    def test_all_errors_returns_none(self):
        readings = [make_reading(error="fail"), make_reading(error="fail2")]
        assert most_urgent(readings) is None

    def test_empty_returns_none(self):
        assert most_urgent([]) is None

    def test_error_readings_excluded_from_selection(self):
        ok = make_reading(sensor="ok", value=25.0, warn=40.0, crit=55.0)
        err = make_reading(sensor="err", value=0.0, error="down")
        result = most_urgent([ok, err])
        assert result is ok


# ── primary_inlet ──────────────────────────────────────────────────────────────

class TestPrimaryInlet:
    def _r(self, sensor, value=25.0):
        return make_reading(sensor=sensor, value=value, warn=40.0, crit=55.0)

    # auto heuristic — tier 0: pure Inlet
    def test_auto_picks_inlet_over_cpu(self):
        inlet = self._r("Inlet", 24.0)
        cpu   = self._r("CPU Temp", 55.0)
        assert primary_inlet([cpu, inlet]) is inlet

    def test_auto_picks_inlet_temp(self):
        r = self._r("Inlet Temp", 24.0)
        assert primary_inlet([r]) is r

    def test_auto_excludes_psu_inlet_from_t0(self):
        psu   = self._r("P/S 1 Inlet", 30.0)
        board = self._r("System Board", 27.0)
        # P/S 1 Inlet matches _INLET_EXCLUDE → goes to tier 3
        # System Board contains "board" → tier 2
        assert primary_inlet([psu, board]) is board

    # auto heuristic — tier 1: Ambient
    def test_auto_picks_ambient_when_no_inlet(self):
        ambient = self._r("Ambient", 24.0)
        cpu     = self._r("CPU Temp", 55.0)
        assert primary_inlet([ambient, cpu]) is ambient

    def test_auto_picks_lowest_in_tier(self):
        i1 = self._r("Inlet", 28.0)
        i2 = self._r("Inlet Temp", 24.0)   # lower → chosen
        assert primary_inlet([i1, i2]) is i2

    # config: exact string
    def test_config_exact_name(self):
        r1 = self._r("01-Inlet Ambient Air", 24.0)
        r2 = self._r("Inlet", 20.0)
        assert primary_inlet([r1, r2], config="01-Inlet Ambient Air") is r1

    def test_config_exact_name_no_match_falls_to_heuristic(self):
        inlet = self._r("Inlet", 24.0)
        result = primary_inlet([inlet], config="NonExistent Sensor")
        assert result is inlet

    # config: substring list
    def test_config_list_first_match_wins(self):
        ambient = self._r("Ambient Temp", 25.0)
        inlet   = self._r("Inlet Temp",   24.0)
        # Pattern "Inlet" matches inlet; "Ambient" would match ambient
        assert primary_inlet([ambient, inlet], config=["Inlet", "Ambient"]) is inlet

    def test_config_list_second_pattern_used_when_first_misses(self):
        ambient = self._r("Ambient Temp", 25.0)
        result = primary_inlet([ambient], config=["Inlet", "Ambient"])
        assert result is ambient

    # all errors
    def test_all_errors_returns_none(self):
        r = make_reading(sensor="x", error="fail")
        assert primary_inlet([r]) is None


# ── alert_hint ─────────────────────────────────────────────────────────────────

class TestAlertHint:
    def _r(self, sensor, value, warn=30.0, crit=40.0):
        return make_reading(sensor=sensor, value=value, warn=warn, crit=crit)

    def test_all_ok_returns_none(self):
        readings = [self._r("Inlet", 25.0), self._r("CPU", 28.0)]
        pri = readings[0]
        assert alert_hint(readings, pri) is None

    def test_primary_is_worst_returns_none(self):
        pri = self._r("Inlet", 45.0)  # CRIT
        other = self._r("CPU", 28.0)
        assert alert_hint([pri, other], pri) is None

    def test_different_worst_returns_sensor_name(self):
        pri  = self._r("Inlet", 25.0)
        bad  = self._r("PSU Temp", 50.0)   # CRIT
        hint = alert_hint([pri, bad], pri)
        assert hint == "PSU Temp"

    def test_long_name_truncated(self):
        pri  = self._r("Inlet", 25.0)
        bad  = self._r("A" * 25, 50.0)    # long sensor name, CRIT
        hint = alert_hint([pri, bad], pri)
        assert hint is not None
        assert len(hint) <= 20

    def test_primary_none_returns_none(self):
        r = self._r("Inlet", 45.0)
        assert alert_hint([r], None) is None


# ── _abbrev_name ───────────────────────────────────────────────────────────────

class TestAbbrevName:
    def test_strips_group_prefix(self):
        assert _abbrev_name("Rack A Bay 3", "Rack A") == "Bay 3"

    def test_strips_dash_separator(self):
        assert _abbrev_name("Rack A - Bay 3", "Rack A") == "Bay 3"

    def test_no_group_returns_full(self):
        assert _abbrev_name("My Server", None) == "My Server"

    def test_no_group_empty_string(self):
        assert _abbrev_name("Server", "") == "Server"
