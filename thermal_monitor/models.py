from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class ThermalReading:
    source: str           # display name of the source
    sensor: str           # sensor label within the source
    value:  float         # temperature in °C  (0.0 when error is set)
    warn:   float         # warning threshold °C
    crit:   float         # critical threshold °C
    error:  Optional[str] = None   # non-None → collection failed

    @property
    def status(self) -> str:
        """One of: OK  WARN  CRIT  ERROR"""
        if self.error:           return "ERROR"
        if self.value >= self.crit:  return "CRIT"
        if self.value >= self.warn:  return "WARN"
        return "OK"

    @property
    def alert_key(self) -> str:
        """Stable key used for alert-cooldown deduplication."""
        return f"{self.source}::{self.sensor}"


STATUS_ORD = {"OK": 0, "WARN": 1, "CRIT": 2, "ERROR": 3}
STATUS_LABEL = {0: "OK", 1: "WARN", 2: "CRIT", 3: "ERROR"}
