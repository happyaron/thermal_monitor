"""Tests for IPMISource."""
from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from thermal_monitor.sources.ipmi import IPMISource


def _src(**kw) -> IPMISource:
    return IPMISource({"name": "ipmi-test", "warn": 40, "crit": 55, **kw})


def _remote_src(**kw) -> IPMISource:
    return _src(host="10.0.0.2", user="admin", password="secret", **kw)


class TestParseSensorList:
    def test_basic(self, ipmi_output):
        src = _src()
        readings = src._parse_sensor_list(ipmi_output)
        sensor_names = [r.sensor for r in readings]
        assert "Inlet Temp" in sensor_names
        assert "CPU1 Temp" in sensor_names
        assert "Exhaust Temp" in sensor_names

    def test_filters_non_temp_units(self, ipmi_output):
        src = _src()
        readings = src._parse_sensor_list(ipmi_output)
        # Fan (RPM) and voltage rows should be excluded
        assert not any("Fan" in r.sensor for r in readings)
        assert not any("Voltage" in r.sensor for r in readings)

    def test_na_value_skipped(self, ipmi_output):
        src = _src()
        readings = src._parse_sensor_list(ipmi_output)
        # "PS1 Sensor" has "na" value — should not appear
        assert not any("PS1" in r.sensor for r in readings)

    def test_sensor_name_filter(self, ipmi_output):
        src = _src(sensors=["Inlet"])
        readings = src._parse_sensor_list(ipmi_output)
        assert all("Inlet" in r.sensor for r in readings)
        assert not any("CPU" in r.sensor for r in readings)

    def test_ipmi_thresholds_extracted(self, ipmi_output):
        src = _src(use_ipmi_thresholds=True)
        readings = src._parse_sensor_list(ipmi_output)
        inlet = next(r for r in readings if r.sensor == "Inlet Temp")
        # Fixture: UNC=42, UCR=47
        assert inlet.warn == 42.0
        assert inlet.crit == 47.0

    def test_hpe_threshold_columns(self, tmp_path):
        # Shifted columns: UNC at col 8, UCR at col 9
        line = "Inlet Temp | 24.000 | degrees C | ok | na | na | na | na | 42.000 | 47.000 | na\n"
        src = _src(use_ipmi_thresholds=True, threshold_columns=[8, 9])
        readings = src._parse_sensor_list(line)
        assert readings[0].warn == 42.0
        assert readings[0].crit == 47.0

    def test_empty_output_returns_error(self):
        src = _src()
        readings = src._parse_sensor_list("")
        assert len(readings) == 1
        assert readings[0].error is not None


class TestIpmitoolEnv:
    def test_password_in_env_for_remote(self):
        src = _remote_src()
        env = src._ipmitool_env()
        assert env is not None
        assert env["IPMI_PASSWORD"] == "secret"

    def test_env_none_for_local(self):
        src = _src()   # no host → local IPMI
        assert src._ipmitool_env() is None

    def test_base_cmd_uses_E_flag(self):
        src = _remote_src()
        cmd = src._base_cmd()
        assert "-E" in cmd
        assert "-P" not in cmd


class TestCollect:
    def test_ipmitool_not_found(self):
        src = _remote_src()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            readings = src.collect()
        assert "not found" in readings[0].error

    def test_ipmitool_timeout(self):
        src = _remote_src()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ipmitool", 30)):
            readings = src.collect()
        assert "timed out" in readings[0].error

    def test_collect_success(self, ipmi_output):
        src = _remote_src()
        mock = MagicMock(returncode=0, stdout=ipmi_output, stderr="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert len(readings) > 0
        assert all(r.error is None for r in readings)
