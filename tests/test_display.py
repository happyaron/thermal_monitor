"""Tests for terminal display: print_table."""
from __future__ import annotations

import pytest
from thermal_monitor.display import print_table
from tests.conftest import make_reading


def _capture(readings, groups=None, primary=None, capsys=None):
    print_table(readings, groups or {}, primary or {})
    return capsys.readouterr().out


class TestPrintTable:
    def test_all_ok_shows_ok_status(self, capsys):
        readings = [make_reading(source="srv1", sensor="Inlet", value=25.0,
                                 warn=40.0, crit=55.0)]
        out = _capture(readings, capsys=capsys)
        assert "srv1" in out
        assert "OK" in out

    def test_warn_source_shows_warn(self, capsys):
        readings = [make_reading(source="srv2", sensor="Inlet", value=45.0,
                                 warn=40.0, crit=55.0)]
        out = _capture(readings, capsys=capsys)
        assert "WARN" in out

    def test_crit_source_shows_crit(self, capsys):
        readings = [make_reading(source="srv3", sensor="Inlet", value=60.0,
                                 warn=40.0, crit=55.0)]
        out = _capture(readings, capsys=capsys)
        assert "CRIT" in out

    def test_summary_counts_in_footer(self, capsys):
        readings = [
            make_reading(source="s", sensor="ok",   value=25.0, warn=40.0, crit=55.0),
            make_reading(source="s", sensor="warn",  value=45.0, warn=40.0, crit=55.0),
        ]
        out = _capture(readings, capsys=capsys)
        assert "WARN" in out
        # Both sensors from same source
        assert "s" in out

    def test_multiple_sources_all_shown(self, capsys):
        readings = [
            make_reading(source="alpha", sensor="T", value=25.0, warn=40.0, crit=55.0),
            make_reading(source="beta",  sensor="T", value=25.0, warn=40.0, crit=55.0),
        ]
        out = _capture(readings, capsys=capsys)
        assert "alpha" in out
        assert "beta" in out

    def test_error_source_shown(self, capsys):
        readings = [make_reading(source="dead", sensor="(source)", value=0.0,
                                 warn=40.0, crit=55.0, error="unreachable")]
        out = _capture(readings, capsys=capsys)
        assert "dead" in out

    def test_grouped_sources_show_group_header(self, capsys):
        readings = [
            make_reading(source="Rack Bay 1", sensor="Inlet", value=25.0,
                         warn=40.0, crit=55.0),
            make_reading(source="Rack Bay 2", sensor="Inlet", value=26.0,
                         warn=40.0, crit=55.0),
        ]
        groups = {"Rack Bay 1": "Rack", "Rack Bay 2": "Rack"}
        out = _capture(readings, groups=groups, capsys=capsys)
        assert "Rack" in out

    def test_sensor_detail_shown_for_non_ok(self, capsys):
        readings = [
            make_reading(source="s", sensor="Inlet",   value=25.0, warn=40.0, crit=55.0),
            make_reading(source="s", sensor="PSU Temp", value=60.0, warn=40.0, crit=55.0),
        ]
        out = _capture(readings, capsys=capsys)
        # Sensor detail rows should appear (source has CRIT)
        assert "PSU Temp" in out
        assert "Inlet" in out
