"""Tests for LocalSensorsSource."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from thermal_monitor.sources.local_sensors import LocalSensorsSource


def _src(**kw) -> LocalSensorsSource:
    return LocalSensorsSource({"name": "test", "warn": 40, "crit": 55, **kw})


class TestParseSensorsJson:
    def test_basic_returns_readings(self, sensors_json):
        src = _src()
        readings = src._parse_sensors_json(sensors_json)
        assert len(readings) > 0
        assert all(r.source == "test" for r in readings)
        assert all(r.error is None for r in readings)

    def test_ambient_only_drops_coretemp(self, sensors_json):
        src = _src(ambient_only=True)
        readings = src._parse_sensors_json(sensors_json)
        sensor_names = [r.sensor for r in readings if not r.error]
        # coretemp sensors should be excluded
        assert not any("Package id" in s or "Core 0" in s for s in sensor_names)

    def test_ambient_only_keeps_acpitz(self, sensors_json):
        src = _src(ambient_only=True)
        readings = src._parse_sensors_json(sensors_json)
        sensor_names = [r.sensor for r in readings if not r.error]
        assert any("acpitz" in s for s in sensor_names)

    def test_chip_filter_includes_only_matching(self, sensors_json):
        src = _src(chips=["acpitz"])
        readings = src._parse_sensors_json(sensors_json)
        assert all("acpitz" in r.sensor for r in readings if not r.error)

    def test_exclude_chips_removes_matching(self, sensors_json):
        src = _src(exclude_chips=["coretemp"])
        readings = src._parse_sensors_json(sensors_json)
        assert not any("coretemp" in r.sensor for r in readings)

    def test_sensor_reported_crit_used(self, sensors_json):
        # acpitz temp1 has temp1_crit: 119.0 in fixture
        src = _src()
        readings = src._parse_sensors_json(sensors_json)
        acpitz = [r for r in readings if "acpitz" in r.sensor and "temp1" in r.sensor.lower()]
        if acpitz:
            assert acpitz[0].crit == 119.0

    def test_no_sensors_returns_error_reading(self):
        src = _src()
        readings = src._parse_sensors_json({})
        assert len(readings) == 1
        assert readings[0].error is not None

    def test_non_temp_subfeatures_skipped(self):
        # fan_input and volt_input should not appear
        data = {
            "nct-isa-0": {
                "Adapter": "ISA adapter",
                "Fan1": {"fan1_input": 3600.0},
                "Temp1": {"temp1_input": 30.0},
            }
        }
        src = _src()
        readings = src._parse_sensors_json(data)
        assert all("Fan" not in r.sensor for r in readings)


class TestCollect:
    def test_sensors_not_found(self):
        src = _src()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            readings = src.collect()
        assert len(readings) == 1
        assert "not found" in readings[0].error

    def test_sensors_timeout(self):
        src = _src()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sensors", 10)):
            readings = src.collect()
        assert "timed out" in readings[0].error

    def test_sensors_nonzero_exit(self):
        src = _src()
        mock = MagicMock(returncode=1, stderr="error msg", stdout="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert readings[0].error is not None

    def test_collect_success(self, sensors_json):
        src = _src()
        mock = MagicMock(returncode=0, stdout=json.dumps(sensors_json), stderr="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert len(readings) > 0
        assert all(r.error is None for r in readings)
