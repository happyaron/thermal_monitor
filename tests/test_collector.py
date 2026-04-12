"""Tests for collector: _apply_sensor_thresholds, _collect_one, collect_all."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from thermal_monitor.collector import _apply_sensor_thresholds, _collect_one, collect_all
from thermal_monitor.models import ThermalReading
from tests.conftest import make_reading


class TestApplySensorThresholds:
    def test_exact_match_overrides(self):
        r = make_reading(sensor="Inlet Temp", warn=30.0, crit=45.0)
        result = _apply_sensor_thresholds([r], {"Inlet Temp": {"warn": 35, "crit": 50}}, "src")
        assert result[0].warn == 35.0
        assert result[0].crit == 50.0

    def test_substring_match(self):
        r = make_reading(sensor="P/S 1 Inlet", warn=30.0, crit=45.0)
        result = _apply_sensor_thresholds([r], {"P/S": {"warn": 45, "crit": 60}}, "src")
        assert result[0].warn == 45.0

    def test_exact_beats_substring(self):
        r = make_reading(sensor="P/S 1 Inlet", warn=30.0, crit=45.0)
        overrides = {
            "P/S": {"warn": 45, "crit": 60},
            "P/S 1 Inlet": {"warn": 50, "crit": 65},
        }
        result = _apply_sensor_thresholds([r], overrides, "src")
        assert result[0].warn == 50.0

    def test_invalid_warn_ge_crit_ignored(self):
        r = make_reading(sensor="T", warn=30.0, crit=45.0)
        result = _apply_sensor_thresholds([r], {"T": {"warn": 60, "crit": 50}}, "src")
        assert result[0].warn == 30.0   # unchanged

    def test_no_match_passes_through(self):
        r = make_reading(sensor="Unmatched", warn=30.0, crit=45.0)
        result = _apply_sensor_thresholds([r], {"Other": {"warn": 50, "crit": 70}}, "src")
        assert result[0] is r   # same object

    def test_partial_override_warn_only(self):
        r = make_reading(sensor="T", warn=30.0, crit=45.0)
        result = _apply_sensor_thresholds([r], {"T": {"warn": 35}}, "src")
        assert result[0].warn == 35.0
        assert result[0].crit == 45.0   # unchanged

    def test_returns_new_list_originals_unchanged(self):
        r = make_reading(sensor="T", warn=30.0, crit=45.0)
        original_warn = r.warn
        _apply_sensor_thresholds([r], {"T": {"warn": 35, "crit": 50}}, "src")
        assert r.warn == original_warn   # original untouched


class TestCollectOne:
    def _mock_source(self, readings=None, name="test", warn=30.0, crit=45.0,
                     sensor_thresholds=None):
        src = MagicMock()
        src.name = name
        src.warn = warn
        src.crit = crit
        src.sensor_thresholds = sensor_thresholds or {}
        src.collect.return_value = readings or [make_reading()]
        return src

    def test_returns_source_readings(self):
        r = make_reading()
        src = self._mock_source([r])
        result = _collect_one(src)
        assert r in result

    def test_applies_sensor_thresholds(self):
        r = make_reading(sensor="T", warn=30.0, crit=45.0)
        src = self._mock_source([r], sensor_thresholds={"T": {"warn": 35, "crit": 50}})
        result = _collect_one(src)
        assert result[0].warn == 35.0

    def test_exception_returns_error_reading(self):
        src = self._mock_source()
        src.collect.side_effect = RuntimeError("boom")
        result = _collect_one(src)
        assert result[0].error is not None
        assert "boom" in result[0].error


class TestCollectAll:
    def test_collects_from_all_sources(self):
        sources = []
        for i in range(3):
            src = MagicMock()
            src.name = f"src{i}"
            src.warn = 30.0
            src.crit = 45.0
            src.sensor_thresholds = {}
            src.collect.return_value = [make_reading(source=f"src{i}")]
            sources.append(src)

        readings = collect_all(sources)
        assert len(readings) == 3
        source_names = {r.source for r in readings}
        assert source_names == {"src0", "src1", "src2"}

    def test_empty_sources(self):
        assert collect_all([]) == []
