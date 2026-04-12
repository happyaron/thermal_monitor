from __future__ import annotations
import re
from typing import List, Optional, Union
from thermal_monitor.models import ThermalReading, STATUS_ORD


def most_urgent(readings: List[ThermalReading]) -> Optional[ThermalReading]:
    """Return the reading closest to (or past) its threshold.

    Priority: highest status tier first; within the same tier, smallest
    headroom to the next threshold (warn−value for OK, crit−value for
    WARN/CRIT — negative when already past crit).
    """
    valid = [r for r in readings if not r.error]
    if not valid:
        return None

    def _key(r):
        tier = STATUS_ORD.get(r.status, 0)
        headroom = (r.warn - r.value) if r.status == "OK" else (r.crit - r.value)
        return (-tier, headroom)   # higher tier first; smaller headroom first

    return min(valid, key=_key)


# Sensors whose names contain these substrings are component-local, not
# representative of room ambient air.
_INLET_EXCLUDE = re.compile(
    r'P/?S\d|PSU|M\.?2|Mezz|CPU|GPU|RAID|NVMe|DIMM|PCH|HDD|SSD|DTS|VR',
    re.IGNORECASE,
)
# Tier 0 — pure inlet: sensor name is essentially "Inlet [Temp|Ambient]"
_INLET_T0 = re.compile(
    r'^(Inlet|Inlet[\s_]*(Temp(erature)?|Ambient))$', re.IGNORECASE,
)
# Tier 1 — pure ambient: "Ambient [Temp]", "System Ambient"
_INLET_T1 = re.compile(
    r'^((System[\s_]*)?Ambient([\s_]*Temp(erature)?)?)$', re.IGNORECASE,
)
# Tier 2 — positional keywords (less specific)
_INLET_T2_KEYWORDS = ("inlet", "ambient", "front", "board", "system")


def primary_inlet(
    readings: List[ThermalReading],
    config: Optional[Union[str, List[str]]] = None,
) -> Optional[ThermalReading]:
    """Select the sensor best representing room ambient temperature.

    *config* overrides the auto heuristic:
      - ``None`` / ``"auto"`` — tiered name heuristic (default)
      - ``"Exact Name"``      — match sensor name exactly
      - ``["Sub1", "Sub2"]``  — ordered substring search, first match wins

    Fallback chain for auto mode:
      T0  pure "Inlet" sensors (excluding component-qualified names)
      T1  pure "Ambient" sensors (same exclusions)
      T2  positional keywords (front, board, system + inlet/ambient)
      T3  any non-excluded sensor
      T4  most_urgent (guaranteed)

    Within each tier, the sensor with the *lowest* reading is chosen —
    closest to true room temperature (component proximity inflates readings).
    """
    valid = [r for r in readings if not r.error]
    if not valid:
        return None

    # ── config-driven override ────────────────────────────────────────────
    if config is not None:
        if isinstance(config, str):
            # Exact sensor name match.
            for r in valid:
                if r.sensor == config:
                    return r
        elif isinstance(config, list):
            # Ordered substring search — first match wins.
            for pattern in config:
                pl = pattern.lower()
                matches = [r for r in valid if pl in r.sensor.lower()]
                if matches:
                    return min(matches, key=lambda r: r.value)
        # Config didn't match anything — fall through to heuristic.

    # ── auto heuristic (tiered) ───────────────────────────────────────────
    tiers: List[List[ThermalReading]] = [[] for _ in range(4)]
    for r in valid:
        name = r.sensor.strip()
        if _INLET_EXCLUDE.search(name):
            tiers[3].append(r)
            continue
        if _INLET_T0.match(name):
            tiers[0].append(r)
        elif _INLET_T1.match(name):
            tiers[1].append(r)
        elif any(kw in name.lower() for kw in _INLET_T2_KEYWORDS):
            tiers[2].append(r)
        else:
            tiers[3].append(r)

    for tier in tiers:
        if tier:
            return min(tier, key=lambda r: r.value)

    # Should not happen if valid is non-empty, but just in case.
    return most_urgent(readings)


def alert_hint(
    readings: List[ThermalReading],
    primary: Optional[ThermalReading],
) -> Optional[str]:
    """Return a short hint string when the worst sensor differs from *primary*.

    Returns ``None`` when the primary sensor IS the worst sensor or
    everything is OK — no hint needed.
    """
    urg = most_urgent(readings)
    if urg is None or primary is None:
        return None
    worst = max(STATUS_ORD.get(r.status, 0) for r in readings)
    if worst == 0:
        return None   # all OK — no hint needed
    if urg.sensor == primary.sensor:
        return None   # primary is the cause — numbers are coherent
    # Truncate long sensor names.
    name = urg.sensor
    if len(name) > 20:
        name = name[:18] + "…"
    return name


def _abbrev_name(full_name: str, group: Optional[str]) -> str:
    """Strip the group prefix from a source name for compact group display."""
    if group:
        prefix = group + " "
        if full_name.startswith(prefix):
            return full_name[len(prefix):].lstrip("- ")
    return full_name
