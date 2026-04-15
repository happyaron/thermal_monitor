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

    def test_global_divisor_zero_rejected_at_init(self):
        """M4: a zero global divisor must fail fast at construction,
        not at collection time."""
        with pytest.raises(ValueError, match="divisor must be non-zero"):
            _src(oids=[{"name": "T", "oid": "1.2.3"}], divisor=0)

    def test_per_oid_zero_divisor_isolated_to_that_oid(self):
        """M4: per-OID divisor=0 must not take out neighbouring OIDs via
        an unhandled ZeroDivisionError from the collector loop."""
        src = _src(oids=[
            {"name": "Bad", "oid": "1.2.3", "divisor": 0},
            {"name": "Good", "oid": "4.5.6"},
        ])
        ok = MagicMock(returncode=0, stdout="28\n", stderr="")
        with patch("subprocess.run", return_value=ok):
            readings = src.collect()
        assert readings[0].error is not None
        assert "division by zero" in readings[0].error.lower()
        assert readings[1].value == 28.0

    def test_warn_ge_crit_is_demoted_in_reading(self, caplog):
        """L3: if sensor/source config somehow ends up with warn >= crit
        (e.g. user mixes up the two), the base _r() helper demotes warn to
        crit-1 so the WARN band is not dead.  Pre-fix, a reading between
        crit and the misconfigured warn would incorrectly stay OK."""
        src = _src(oids=[{"name": "T", "oid": "1.2.3"}], warn=50, crit=45)
        # Pick a value that lands just under crit — this is the regime where
        # the old behaviour (warn never triggers because value < warn=50) fell
        # through to OK even though the device is running hotter than crit-1.
        mock = MagicMock(returncode=0, stdout="44.5\n", stderr="")
        with caplog.at_level("WARNING"):
            with patch("subprocess.run", return_value=mock):
                readings = src.collect()
        r = readings[0]
        assert r.warn < r.crit
        assert r.warn == 44.0   # crit(45) - 1
        # 44.5 is >= warn(44), < crit(45)  →  WARN, not OK.
        assert r.status == "WARN"

    def test_nan_reading_is_error_not_ok(self):
        """M1: float('nan') is a valid float literal — "nan" from a
        device would pass ValueError parsing and then compare False
        against any threshold, silently reporting OK without this guard."""
        src = _src(oids=[{"name": "T", "oid": "1.2.3"}])
        mock = MagicMock(returncode=0, stdout="nan\n", stderr="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert readings[0].error is not None
        assert "non-finite" in readings[0].error
