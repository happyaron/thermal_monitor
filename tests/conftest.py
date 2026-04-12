"""Shared pytest fixtures."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from thermal_monitor.models import ThermalReading

FIXTURES = Path(__file__).parent / "fixtures"


# ── reading factory ────────────────────────────────────────────────────────────

def make_reading(
    source: str = "src",
    sensor: str = "sensor",
    value: float = 25.0,
    warn: float = 40.0,
    crit: float = 55.0,
    error: str | None = None,
) -> ThermalReading:
    return ThermalReading(
        source=source, sensor=sensor, value=value,
        warn=warn, crit=crit, error=error,
    )


@pytest.fixture
def ok_reading():
    return make_reading(value=25.0, warn=40.0, crit=55.0)


@pytest.fixture
def warn_reading():
    return make_reading(value=42.0, warn=40.0, crit=55.0)


@pytest.fixture
def crit_reading():
    return make_reading(value=60.0, warn=40.0, crit=55.0)


@pytest.fixture
def error_reading():
    return make_reading(value=0.0, error="collection failed")


@pytest.fixture
def mixed_readings():
    """Three sources: one OK, one WARN, one ERROR."""
    return [
        make_reading(source="srv1", sensor="Inlet", value=24.0, warn=30.0, crit=38.0),
        make_reading(source="srv1", sensor="CPU",   value=55.0, warn=70.0, crit=85.0),
        make_reading(source="srv2", sensor="Inlet", value=35.0, warn=30.0, crit=38.0),
        make_reading(source="srv3", sensor="(source)", value=0.0,
                     warn=30.0, crit=38.0, error="timeout"),
    ]


# ── fixture file helpers ───────────────────────────────────────────────────────

@pytest.fixture
def sensors_json():
    return json.loads((FIXTURES / "sample_sensors_json.json").read_text())


@pytest.fixture
def ipmi_output():
    return (FIXTURES / "sample_ipmi_output.txt").read_text()


@pytest.fixture
def redfish_thermal():
    return json.loads((FIXTURES / "sample_redfish_thermal.json").read_text())


@pytest.fixture
def minimal_config_path(tmp_path):
    """Copy the minimal config fixture to a tmp_path and return its path."""
    src = FIXTURES / "minimal_config.yaml"
    dst = tmp_path / "config.yaml"
    dst.write_text(src.read_text())
    return str(dst)


# ── ANSI suppression ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_ansi(monkeypatch):
    """Disable ANSI color codes for all tests (predictable string output)."""
    import thermal_monitor._ansi as ansi
    monkeypatch.setattr(ansi, "_COLOR", False)
