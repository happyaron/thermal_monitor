from __future__ import annotations
import logging
import subprocess
from typing import Dict, List
from thermal_monitor.sources.base import ThermalSource
from thermal_monitor.models import ThermalReading

log = logging.getLogger(__name__)


class SNMPSource(ThermalSource):
    """
    Poll temperature OIDs via the ``snmpget`` CLI tool.

    Each OID must be listed explicitly with a human-readable name.
    Some vendors encode temperature as integer × 10; set ``divisor: 10`` for those.

    Common OIDs:
        Cisco CISCO-ENVMON-MIB::ciscoEnvMonTemperatureStatusValue.N
            1.3.6.1.4.1.9.9.13.1.3.1.3.N
        Juniper JUNIPER-MIB::jnxOperatingTemp.7.N.0.0
            1.3.6.1.4.1.2636.3.1.13.1.7.7.N.0.0
        Generic ENTITY-SENSOR-MIB (RFC 3433)
            1.3.6.1.2.1.99.1.1.1.4.N  (entPhySensorValue)

    Config keys:
        host        — device IP/hostname  (required)
        community   — SNMP community string  (default: public)
        version     — SNMP version string: 2c or 1  (default: 2c)
        oids        — list of {name, oid, divisor?} entries  (required)
        divisor     — global divisor applied to all OIDs  (default: 1)
    """

    def __init__(self, cfg: dict):
        super().__init__(
            name=cfg.get("name", "snmp"),
            warn=float(cfg.get("warn", 45)),
            crit=float(cfg.get("crit", 60)),
        )
        self.host      = cfg["host"]
        self.community = cfg.get("community", "public")
        self.version   = str(cfg.get("version", "2c"))
        self.oids: List[Dict] = cfg.get("oids", [])
        self.default_divisor = float(cfg.get("divisor", 1.0))

    def _snmp_common(self) -> List[str]:
        return ["-v", self.version, "-c", self.community, self.host]

    def collect(self) -> List[ThermalReading]:
        if not self.oids:
            return self._errs("no OIDs configured")

        readings: List[ThermalReading] = []
        for entry in self.oids:
            name    = entry.get("name", entry.get("oid", "?"))
            oid     = entry["oid"]
            divisor = float(entry.get("divisor", self.default_divisor))

            log.debug("[%s] snmpget -v%s -c *** %s %s", self.name, self.version, self.host, oid)
            try:
                r = subprocess.run(
                    ["snmpget", "-Oqv"] + self._snmp_common() + [oid],
                    capture_output=True, text=True, timeout=10,
                )
            except FileNotFoundError:
                return self._errs("snmpget not found — install the snmp package")
            except subprocess.TimeoutExpired:
                readings.append(self._err(name, f"snmpget timed out ({self.host})"))
                continue

            if r.returncode != 0:
                readings.append(self._err(name, f"snmpget error: {r.stderr.strip()[:80]}"))
                continue

            raw = r.stdout.strip()
            try:
                value = float(raw) / divisor
            except ValueError:
                readings.append(self._err(name, f"non-numeric SNMP value: {raw!r}"))
                continue

            log.debug("[%s] %s → raw=%s  value=%.1f°C", self.name, name, raw, value)
            readings.append(self._r(name, value))
        return readings
