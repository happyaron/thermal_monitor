from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union
from thermal_monitor.models import ThermalReading, STATUS_ORD, STATUS_LABEL
from thermal_monitor.analysis import primary_inlet, alert_hint, _abbrev_name


def readings_to_dict(
    readings: List[ThermalReading],
    source_groups: Optional[Dict[str, str]] = None,
    primary_sensors: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> dict:
    """Serialize a snapshot of readings to a plain dict (JSON-ready).

    Each source entry includes ``primary_temp`` (the ambient inlet reading)
    and ``alert_hint`` (sensor name when the alert cause differs from
    the primary sensor, or ``null``).
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    source_groups = source_groups or {}
    primary_sensors = primary_sensors or {}

    by_source: Dict[str, List[ThermalReading]] = {}
    for r in readings:
        by_source.setdefault(r.source, []).append(r)

    def _sort_key(name: str):
        grp = source_groups.get(name)
        return (1 if grp else 0, grp or "", name)

    sources = []
    for name in sorted(by_source, key=_sort_key):
        src_readings = by_source[name]
        valid = [r for r in src_readings if not r.error]
        worst = max(STATUS_ORD[r.status] for r in src_readings)
        sensors = []
        for r in sorted(src_readings, key=lambda x: x.sensor):
            s: dict = {
                "name": r.sensor,
                "value": r.value if not r.error else None,
                "warn": r.warn,
                "crit": r.crit,
                "status": r.status,
            }
            if r.error:
                s["error"] = r.error
            sensors.append(s)

        pri = primary_inlet(src_readings, primary_sensors.get(name))
        entry: dict = {
            "name": name,
            "status": STATUS_LABEL[worst],
            "max_temp": round(max(r.value for r in valid), 1) if valid else None,
            "primary_temp": round(pri.value, 1) if pri else None,
            "primary_warn": pri.warn if pri else None,
            "primary_crit": pri.crit if pri else None,
            "alert_hint": alert_hint(src_readings, pri),
            "sensors": sensors,
        }
        grp = source_groups.get(name)
        if grp:
            entry["group"] = grp
            entry["short_name"] = _abbrev_name(name, grp)
        sources.append(entry)

    return {
        "timestamp": ts,
        "sources": sources,
        "summary": {
            "total_sources": len(by_source),
            "total_sensors": len(readings),
            "ok":    sum(1 for r in readings if r.status == "OK"),
            "warn":  sum(1 for r in readings if r.status == "WARN"),
            "crit":  sum(1 for r in readings if r.status == "CRIT"),
            "error": sum(1 for r in readings if r.status == "ERROR"),
        },
    }
