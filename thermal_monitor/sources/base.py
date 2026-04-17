from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union
from thermal_monitor.models import ThermalReading

log = logging.getLogger(__name__)


class ThermalSource(ABC):
    def __init__(self, name: str, warn: float, crit: float):
        self.name = name
        self.warn = warn
        self.crit = crit
        # Per-sensor threshold overrides — keyed by exact sensor name as it
        # appears in ThermalReading.sensor.  Applied after collect() returns.
        # Config key: sensor_thresholds: {"Sensor Name": {warn: N, crit: N}}
        self.sensor_thresholds: Dict[str, Dict] = {}
        # Substring-match fallback for sensor_thresholds.  Each entry is a dict
        # with a "contains" key (case-insensitive) plus warn/crit.  First match
        # wins.  Exact sensor_thresholds takes priority over any pattern.
        # Config key: sensor_patterns: [{contains: "P/S", warn: N, crit: N}]
        self.sensor_patterns: List[Dict] = []
        # Set by load_config for host_range-expanded sources: the shared name
        # prefix used for group headers in display output (e.g. "HW Storage").
        self.group: Optional[str] = None
        # Primary sensor selection for summary display.
        # "auto" (default) → tiered heuristic; str → exact name; list → ordered substrings.
        self.primary_sensor: Optional[Union[str, List[str]]] = None

    @abstractmethod
    def collect(self) -> List[ThermalReading]:
        """Return readings.  Must never raise — return error readings instead."""
        ...

    # ── conveniences ──────────────────────────────────────────────────────

    def _r(self, sensor: str, value: float,
           warn: Optional[float] = None,
           crit: Optional[float] = None) -> ThermalReading:
        resolved_warn = warn if warn is not None else self.warn
        resolved_crit = crit if crit is not None else self.crit
        if resolved_warn >= resolved_crit:
            # Can happen when a sensor-reported UCR is below the configured
            # warn floor, or when config sets warn >= crit by mistake.
            # Downgrade warn so WARN status can still trigger before CRIT —
            # otherwise the whole WARN band is dead.
            log.warning(
                "[%s] %s: resolved warn=%.1f >= crit=%.1f — "
                "adjusting warn to crit-1 so WARN status can trigger",
                self.name, sensor, resolved_warn, resolved_crit,
            )
            resolved_warn = resolved_crit - 1
        return ThermalReading(
            source=self.name,
            sensor=sensor,
            value=round(value, 1),
            warn=resolved_warn,
            crit=resolved_crit,
        )

    def _err(self, sensor: str, msg: str) -> ThermalReading:
        return ThermalReading(
            source=self.name, sensor=sensor,
            value=0.0, warn=self.warn, crit=self.crit,
            error=msg,
        )

    def _errs(self, msg: str) -> List[ThermalReading]:
        """Return a single-element error list (whole-source failure)."""
        return [self._err("(source)", msg)]
