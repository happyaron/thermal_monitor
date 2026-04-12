"""Tests for readings_to_dict JSON serialization."""
from __future__ import annotations

import pytest
from thermal_monitor.serialization import readings_to_dict
from tests.conftest import make_reading


class TestReadingsToDict:
    def _call(self, readings, groups=None, primary=None):
        return readings_to_dict(readings, groups or {}, primary or {})

    def test_top_level_keys(self):
        r = make_reading()
        result = self._call([r])
        assert set(result.keys()) == {"timestamp", "sources", "summary"}

    def test_timestamp_utc_format(self):
        r = make_reading()
        ts = self._call([r])["timestamp"]
        # ISO 8601 UTC: ends with Z
        assert ts.endswith("Z")
        assert "T" in ts

    def test_summary_counts(self):
        readings = [
            make_reading(sensor="ok",   value=25.0, warn=40.0, crit=55.0),
            make_reading(sensor="warn", value=45.0, warn=40.0, crit=55.0),
            make_reading(sensor="crit", value=60.0, warn=40.0, crit=55.0),
            make_reading(sensor="err",  value=0.0, error="down"),
        ]
        s = self._call(readings)["summary"]
        assert s["ok"]    == 1
        assert s["warn"]  == 1
        assert s["crit"]  == 1
        assert s["error"] == 1

    def test_source_entry_fields(self):
        r = make_reading(source="srv", sensor="Inlet", value=24.0, warn=30.0, crit=40.0)
        sources = self._call([r])["sources"]
        entry = sources[0]
        assert entry["name"] == "srv"
        assert entry["status"] == "OK"
        assert entry["primary_temp"] == 24.0
        assert len(entry["sensors"]) == 1

    def test_error_sensor_value_is_null(self):
        r = make_reading(sensor="t", value=0.0, error="fail")
        sources = self._call([r])["sources"]
        sensor = sources[0]["sensors"][0]
        assert sensor["value"] is None
        assert sensor["error"] == "fail"

    def test_group_fields_present(self):
        r = make_reading(source="Rack Bay 1", sensor="Inlet", value=24.0)
        groups = {"Rack Bay 1": "Rack"}
        sources = self._call([r], groups=groups)["sources"]
        entry = sources[0]
        assert entry["group"] == "Rack"
        assert "short_name" in entry

    def test_primary_temp_uses_inlet(self):
        inlet = make_reading(sensor="Inlet", value=24.0, warn=30.0, crit=40.0)
        cpu   = make_reading(sensor="CPU",   value=55.0, warn=70.0, crit=85.0)
        # Both same source
        inlet = make_reading(source="s", sensor="Inlet", value=24.0, warn=30.0, crit=40.0)
        cpu   = make_reading(source="s", sensor="CPU",   value=55.0, warn=70.0, crit=85.0)
        sources = self._call([inlet, cpu])["sources"]
        assert sources[0]["primary_temp"] == 24.0

    def test_alert_hint_populated_when_worst_differs(self):
        pri = make_reading(source="s", sensor="Inlet",   value=25.0, warn=30.0, crit=40.0)
        bad = make_reading(source="s", sensor="PSU Temp", value=50.0, warn=30.0, crit=40.0)
        sources = self._call([pri, bad])["sources"]
        assert sources[0]["alert_hint"] == "PSU Temp"

    def test_total_counts(self):
        readings = [make_reading(source="a"), make_reading(source="b")]
        s = self._call(readings)["summary"]
        assert s["total_sources"] == 2
        assert s["total_sensors"] == 2
