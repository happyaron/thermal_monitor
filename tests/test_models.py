"""Tests for ThermalReading data model and status constants."""
from __future__ import annotations

import pytest
from thermal_monitor.models import ThermalReading, STATUS_ORD, STATUS_LABEL
from tests.conftest import make_reading


class TestThermalReadingStatus:
    def test_ok(self):
        assert make_reading(value=25.0, warn=40.0, crit=55.0).status == "OK"

    def test_warn_at_boundary(self):
        assert make_reading(value=40.0, warn=40.0, crit=55.0).status == "WARN"

    def test_warn_above_boundary(self):
        assert make_reading(value=41.0, warn=40.0, crit=55.0).status == "WARN"

    def test_crit_at_boundary(self):
        assert make_reading(value=55.0, warn=40.0, crit=55.0).status == "CRIT"

    def test_crit_above_boundary(self):
        assert make_reading(value=80.0, warn=40.0, crit=55.0).status == "CRIT"

    def test_error_overrides_value(self):
        # Even if value would be OK, error field wins.
        r = make_reading(value=25.0, warn=40.0, crit=55.0, error="timeout")
        assert r.status == "ERROR"

    def test_error_overrides_crit_value(self):
        r = make_reading(value=99.0, warn=40.0, crit=55.0, error="timeout")
        assert r.status == "ERROR"

    def test_ok_just_below_warn(self):
        r = make_reading(value=39.9, warn=40.0, crit=55.0)
        assert r.status == "OK"

    def test_warn_just_below_crit(self):
        r = make_reading(value=54.9, warn=40.0, crit=55.0)
        assert r.status == "WARN"

    def test_nan_value_is_error(self):
        # M1: NaN >= threshold is always False, which would silently report
        # a broken sensor as OK.  The model treats non-finite as ERROR.
        r = make_reading(value=float("nan"), warn=40.0, crit=55.0)
        assert r.status == "ERROR"

    def test_inf_value_is_error(self):
        r = make_reading(value=float("inf"), warn=40.0, crit=55.0)
        assert r.status == "ERROR"

    def test_negative_inf_value_is_error(self):
        r = make_reading(value=float("-inf"), warn=40.0, crit=55.0)
        assert r.status == "ERROR"


class TestAlertKey:
    def test_format(self):
        r = make_reading(source="MyServer", sensor="Inlet Temp")
        assert r.alert_key == "MyServer::Inlet Temp"

    def test_unique_across_source_sensor(self):
        r1 = make_reading(source="A", sensor="X")
        r2 = make_reading(source="A", sensor="Y")
        r3 = make_reading(source="B", sensor="X")
        assert len({r1.alert_key, r2.alert_key, r3.alert_key}) == 3

    def test_stable(self):
        r = make_reading(source="s", sensor="t")
        assert r.alert_key == r.alert_key


class TestStatusConstants:
    def test_ord_covers_all_statuses(self):
        for status in ("OK", "WARN", "CRIT", "ERROR"):
            assert status in STATUS_ORD

    def test_ord_ordering(self):
        assert STATUS_ORD["OK"] < STATUS_ORD["WARN"]
        assert STATUS_ORD["WARN"] < STATUS_ORD["CRIT"]
        assert STATUS_ORD["CRIT"] < STATUS_ORD["ERROR"]

    def test_label_is_inverse_of_ord(self):
        for status, ordinal in STATUS_ORD.items():
            assert STATUS_LABEL[ordinal] == status
