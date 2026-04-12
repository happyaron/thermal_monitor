"""Tests for SNMPSource."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from thermal_monitor.sources.snmp import SNMPSource


def _src(**kw) -> SNMPSource:
    return SNMPSource({"name": "snmp-test", "host": "10.0.0.4",
                       "warn": 45, "crit": 60, **kw})


class TestCollect:
    def test_no_oids_returns_error(self):
        src = _src(oids=[])
        readings = src.collect()
        assert readings[0].error is not None

    def test_basic_success(self):
        src = _src(oids=[{"name": "Intake", "oid": "1.3.6.1.4.1.9.9.13.1.3.1.3.1"}])
        mock = MagicMock(returncode=0, stdout="32\n", stderr="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert readings[0].value == 32.0
        assert readings[0].sensor == "Intake"

    def test_divisor_applied(self):
        src = _src(oids=[{"name": "T", "oid": "1.2.3", "divisor": 10}])
        mock = MagicMock(returncode=0, stdout="320\n", stderr="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert readings[0].value == 32.0

    def test_global_divisor_applied(self):
        src = _src(oids=[{"name": "T", "oid": "1.2.3"}], divisor=10)
        mock = MagicMock(returncode=0, stdout="250\n", stderr="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert readings[0].value == 25.0

    def test_non_numeric_value_is_error(self):
        src = _src(oids=[{"name": "T", "oid": "1.2.3"}])
        mock = MagicMock(returncode=0, stdout="No Such Object\n", stderr="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert readings[0].error is not None

    def test_snmpget_not_found(self):
        src = _src(oids=[{"name": "T", "oid": "1.2.3"}])
        with patch("subprocess.run", side_effect=FileNotFoundError):
            readings = src.collect()
        assert "not found" in readings[0].error

    def test_snmpget_timeout_continues_to_next(self):
        src = _src(oids=[
            {"name": "T1", "oid": "1.2.3"},
            {"name": "T2", "oid": "4.5.6"},
        ])
        ok = MagicMock(returncode=0, stdout="30\n", stderr="")
        with patch("subprocess.run", side_effect=[
            subprocess.TimeoutExpired("snmpget", 10),
            ok,
        ]):
            readings = src.collect()
        assert readings[0].error is not None   # T1 timed out
        assert readings[1].value == 30.0       # T2 succeeded
