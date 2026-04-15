"""Tests for RedfishSource."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from thermal_monitor.sources.redfish import RedfishSource


def _src(**kw) -> RedfishSource:
    return RedfishSource({"name": "rf-test", "host": "10.0.0.3",
                          "user": "root", "password": "pw",
                          "verify_ssl": False, "warn": 30, "crit": 42, **kw})


class TestCollect:
    def test_basic_readings(self, redfish_thermal):
        src = _src()
        with patch.object(src, "_chassis_ids", return_value=["1"]), \
             patch.object(src, "_get", return_value=redfish_thermal):
            readings = src.collect()
        sensor_names = [r.sensor for r in readings]
        assert "Inlet Temp" in sensor_names
        assert "CPU1 Temp" in sensor_names

    def test_absent_sensor_skipped(self, redfish_thermal):
        src = _src()
        with patch.object(src, "_chassis_ids", return_value=["1"]), \
             patch.object(src, "_get", return_value=redfish_thermal):
            readings = src.collect()
        assert not any(r.sensor == "Absent Sensor" for r in readings)

    def test_sensor_filter(self, redfish_thermal):
        src = _src(sensors=["Inlet"])
        with patch.object(src, "_chassis_ids", return_value=["1"]), \
             patch.object(src, "_get", return_value=redfish_thermal):
            readings = src.collect()
        assert all("Inlet" in r.sensor for r in readings)

    def test_redfish_thresholds_applied(self, redfish_thermal):
        src = _src(use_redfish_thresholds=True)
        with patch.object(src, "_chassis_ids", return_value=["1"]), \
             patch.object(src, "_get", return_value=redfish_thermal):
            readings = src.collect()
        inlet = next(r for r in readings if r.sensor == "Inlet Temp")
        assert inlet.warn == 35.0
        assert inlet.crit == 42.0

    def test_ucr_only_derives_warn(self, redfish_thermal):
        # Modify fixture: "No Thresholds" has null UNC, null UCR — add only UCR
        data = {
            "@odata.id": "/redfish/v1/Chassis/1/Thermal",
            "Temperatures": [{
                "Name": "Test Sensor",
                "ReadingCelsius": 28.0,
                "UpperThresholdNonCritical": None,
                "UpperThresholdCritical": 50.0,
                "Status": {"State": "Enabled"},
            }]
        }
        src = _src(use_redfish_thresholds=True, warn=30, crit=42)
        with patch.object(src, "_chassis_ids", return_value=["1"]), \
             patch.object(src, "_get", return_value=data):
            readings = src.collect()
        r = readings[0]
        assert r.crit == 50.0
        # warn = max(config_warn=30, crit-15=35) = 35
        assert r.warn == 35.0

    def test_ssl_context_cached(self):
        src = _src()
        ctx1 = src._ssl_ctx()
        ctx2 = src._ssl_ctx()
        assert ctx1 is ctx2

    def test_chassis_exclude(self):
        src = _src(chassis_exclude=["RAID"])
        chassis_data = {
            "Members": [
                {"@odata.id": "/redfish/v1/Chassis/1"},
                {"@odata.id": "/redfish/v1/Chassis/RAID.Enclosure"},
            ]
        }
        with patch.object(src, "_get", return_value=chassis_data):
            ids = src._chassis_ids()
        assert "1" in ids
        assert not any("RAID" in cid for cid in ids)

    def test_chassis_discovery_failure_returns_error(self):
        src = _src()
        with patch.object(src, "_chassis_ids", side_effect=Exception("unreachable")):
            readings = src.collect()
        assert readings[0].error is not None

    def test_empty_temperatures_returns_error(self):
        src = _src()
        with patch.object(src, "_chassis_ids", return_value=["1"]), \
             patch.object(src, "_get", return_value={"Temperatures": []}):
            readings = src.collect()
        assert readings[0].error is not None

    def test_nan_reading_skipped(self):
        """M1: a NaN ReadingCelsius must not produce a reading that
        later gets reported as OK (NaN >= threshold is always False)."""
        data = {
            "Temperatures": [
                {"Name": "Broken", "ReadingCelsius": float("nan"),
                 "Status": {"State": "Enabled"}},
                {"Name": "Good", "ReadingCelsius": 25.0,
                 "Status": {"State": "Enabled"}},
            ]
        }
        src = _src()
        with patch.object(src, "_chassis_ids", return_value=["1"]), \
             patch.object(src, "_get", return_value=data):
            readings = src.collect()
        names = [r.sensor for r in readings]
        assert "Broken" not in names
        assert "Good" in names


class TestBodyCap:
    def test_oversized_body_raises(self):
        """M3: the raw HTTP read is capped at 4 MB to prevent a hostile
        BMC from streaming garbage into memory."""
        from thermal_monitor.sources.redfish import _MAX_BODY_BYTES
        src = _src()
        # Simulate a response stream that returns more than the cap when
        # asked for MAX+1 bytes — this mimics a server sending an oversize
        # body that we haven't consumed yet.
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"x" * (_MAX_BODY_BYTES + 1)
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=fake_resp):
            with pytest.raises(IOError, match="exceeded"):
                src._get("/redfish/v1/Chassis")
