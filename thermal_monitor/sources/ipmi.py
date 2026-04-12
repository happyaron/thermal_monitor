from __future__ import annotations
import logging
import os
import subprocess
from typing import Dict, List, Optional
from thermal_monitor.sources.base import ThermalSource
from thermal_monitor.models import ThermalReading

log = logging.getLogger(__name__)


class IPMISource(ThermalSource):
    """
    Read temperature sensors via ``ipmitool sensor list``.

    Supports both local IPMI (no host) and remote (host + credentials).
    The full sensor list is fetched in one call; rows are filtered to
    "degrees C" units and optionally to a sensor name allow-list.

    ipmitool sensor list columns:
        Name | Value | Unit | Status | LNR | LCR | LNC | UNC | UCR | UNR

    Where UNC = Upper Non-Critical (≈warn) and UCR = Upper Critical (≈crit).

    Config keys:
        host                — BMC IP/hostname  (omit for local IPMI)
        user                — IPMI username
        password            — IPMI password
        interface           — lanplus (default) | lan | local
        sensors             — list of sensor-name substrings to include (optional)
        use_ipmi_thresholds — use UNC/UCR from SDR instead of warn/crit  (default: true)
        threshold_columns   — [unc_col, ucr_col] indices in sensor list row
                              standard ipmitool: [7, 8] (default)
                              some HPE ProLiant:  [8, 9]
    """

    def __init__(self, cfg: dict):
        super().__init__(
            name=cfg.get("name", "ipmi"),
            warn=float(cfg.get("warn", 40)),
            crit=float(cfg.get("crit", 55)),
        )
        self.host      = cfg.get("host")
        self.user      = cfg.get("user", "admin")
        self.password  = cfg.get("password", "")
        self.interface = cfg.get("interface", "lanplus")
        self.sensors_filter: List[str] = cfg.get("sensors", [])
        self.use_ipmi_thresholds: bool = bool(cfg.get("use_ipmi_thresholds", True))
        # Column indices for UNC (warn) and UCR (crit) in `ipmitool sensor list`.
        # Standard ipmitool: [7, 8].  Some HPE ProLiant systems: [8, 9].
        tc = cfg.get("threshold_columns", [7, 8])
        self.thresh_unc_col: int = int(tc[0])
        self.thresh_ucr_col: int = int(tc[1])

    def _base_cmd(self) -> List[str]:
        cmd = ["ipmitool"]
        if self.host:
            cmd += [
                "-H", self.host,
                "-I", self.interface,
                "-L", "USER",
                "-U", self.user,
                "-E",  # read password from IPMI_PASSWORD env var (avoids ps exposure)
            ]
        return cmd

    def _ipmitool_env(self) -> Optional[dict]:
        """Return env dict with IPMI_PASSWORD set, or None for local IPMI."""
        if self.host and self.password:
            return {**os.environ, "IPMI_PASSWORD": self.password}
        return None

    def collect(self) -> List[ThermalReading]:
        cmd = self._base_cmd() + ["sensor", "list"]
        log.debug("[%s] running: %s", self.name, " ".join(cmd))
        env = self._ipmitool_env()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        except FileNotFoundError:
            return self._errs("ipmitool not found — install ipmitool")
        except subprocess.TimeoutExpired:
            return self._errs(f"ipmitool timed out ({self.host or 'local'})")

        if result.returncode != 0:
            msg = result.stderr.strip()[:120]
            return self._errs(f"ipmitool failed ({result.returncode}): {msg}")
        readings = self._parse_sensor_list(result.stdout)
        log.debug("[%s] got %d reading(s)", self.name, len(readings))
        return readings

    def _parse_sensor_list(self, output: str) -> List[ThermalReading]:
        readings: List[ThermalReading] = []
        for line in output.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            unit = parts[2].lower()
            if "degrees c" not in unit and "celsius" not in unit:
                continue
            sensor_name = parts[0]
            if self.sensors_filter and not any(
                f.lower() in sensor_name.lower() for f in self.sensors_filter
            ):
                continue
            try:
                value = float(parts[1])
            except ValueError:
                continue   # "na" — sensor not readable right now

            # Try to extract UNC and UCR from the SDR.
            warn, crit = self.warn, self.crit
            need_cols = self.thresh_ucr_col + 1   # need at least up to UCR col
            if self.use_ipmi_thresholds and len(parts) >= need_cols:
                def _thresh(s: str) -> Optional[float]:
                    try:
                        v = float(s)
                        return v if 0 < v < 300 else None
                    except ValueError:
                        return None

                unc = _thresh(parts[self.thresh_unc_col])
                ucr = _thresh(parts[self.thresh_ucr_col])
                if ucr is not None:
                    crit = ucr
                # Only apply UNC as warn if it is below the effective crit,
                # so warn < crit is always guaranteed.
                if unc is not None and unc < crit:
                    warn = unc

            readings.append(self._r(sensor_name, value, warn=warn, crit=crit))
        return readings or self._errs("no temperature sensors in ipmitool output")
