from __future__ import annotations
import base64
import json
import logging
import math
import ssl
import urllib.error
import urllib.request
from typing import List, Optional
from thermal_monitor.sources.base import ThermalSource
from thermal_monitor.models import ThermalReading

log = logging.getLogger(__name__)

# Hard cap on Redfish response body size.  Normal /Thermal payloads are a
# few KB even on large chassis; anything over 4 MB is a misbehaving or
# hostile BMC streaming garbage into our memory.  We still read up to this
# cap because the 15 s timeout on urlopen() only bounds socket idle, not
# total bytes transferred.
_MAX_BODY_BYTES = 4 * 1024 * 1024


class RedfishSource(ThermalSource):
    """
    Read temperatures from a Redfish-compliant BMC.
    Works with Dell iDRAC, HPE iLO 4/5/6, OpenBMC, Supermicro, Lenovo XCC, …

    Endpoint queried: GET /redfish/v1/Chassis/{chassis_id}/Thermal
    Response field:   .Temperatures[].ReadingCelsius

    Config keys:
        host                    — BMC IP/hostname  (required)
        user                    — Redfish username  (default: root)
        password                — Redfish password
        chassis                 — chassis ID  (auto-discovered from /Chassis if omitted)
        chassis_exclude         — list of chassis-ID substrings to skip during
                                  auto-discovery (e.g. ["RAID", "Enclosure"]);
                                  useful for storage/RAID enclosures whose
                                  /Thermal endpoint is absent or unresponsive
        verify_ssl              — verify TLS certificate  (default: true)
        sensors                 — list of sensor-name substrings to include  (optional)
        use_redfish_thresholds  — use UpperThresholdNonCritical/Critical from API
                                  (default: true; falls back to warn/crit if absent)
                                  When only UCR is present (UNC is null), warn is
                                  derived as max(config_warn, UCR − 15): the
                                  config warn acts as a floor so ambient sensors
                                  with low UCR still get meaningful headroom.
    """

    def __init__(self, cfg: dict):
        super().__init__(
            name=cfg.get("name", "redfish"),
            warn=float(cfg.get("warn", 40)),
            crit=float(cfg.get("crit", 55)),
        )
        self.host       = cfg["host"]
        self.user       = cfg.get("user", "root")
        self.password   = cfg.get("password", "")
        self.chassis    = cfg.get("chassis")   # None → auto-discover
        self.chassis_exclude: List[str] = cfg.get("chassis_exclude", [])
        self.verify_ssl = bool(cfg.get("verify_ssl", True))
        self.sensors_filter: List[str] = cfg.get("sensors", [])
        self.use_redfish_thresholds: bool = bool(cfg.get("use_redfish_thresholds", True))
        self._base_url = f"https://{self.host}"
        self._ssl_context: Optional[ssl.SSLContext] = None

    def _ssl_ctx(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            ctx = ssl.create_default_context()
            if not self.verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self._ssl_context = ctx
        return self._ssl_context

    def _get(self, path: str) -> dict:
        url = self._base_url + path
        req = urllib.request.Request(url)
        cred = base64.b64encode(
            f"{self.user}:{self.password}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {cred}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, context=self._ssl_ctx(), timeout=15) as resp:
            # Read one byte past the cap so we can detect overflow without
            # pulling the whole thing into memory.
            body = resp.read(_MAX_BODY_BYTES + 1)
            if len(body) > _MAX_BODY_BYTES:
                raise IOError(
                    f"Redfish response body exceeded {_MAX_BODY_BYTES} bytes"
                )
            return json.loads(body.decode())

    def _chassis_ids(self) -> List[str]:
        if self.chassis:
            return [self.chassis]
        data = self._get("/redfish/v1/Chassis")
        ids = []
        for m in data.get("Members", []):
            if isinstance(m, dict):
                odata_id = m.get("@odata.id", "")
            elif isinstance(m, str):
                odata_id = m
            else:
                continue
            cid = odata_id.rstrip("/").split("/")[-1]
            if cid:
                ids.append(cid)
        if self.chassis_exclude:
            filtered = [
                cid for cid in ids
                if not any(ex.lower() in cid.lower() for ex in self.chassis_exclude)
            ]
            skipped = [cid for cid in ids if cid not in filtered]
            if skipped:
                log.debug("[%s] chassis excluded: %s", self.name, skipped)
            ids = filtered
        return ids

    def collect(self) -> List[ThermalReading]:
        log.debug("[%s] discovering chassis at https://%s/redfish/v1/Chassis", self.name, self.host)
        try:
            chassis_ids = self._chassis_ids()
        except Exception as exc:
            return self._errs(f"Redfish chassis discovery failed: {exc}")

        log.debug("[%s] chassis found: %s", self.name, chassis_ids)
        readings: List[ThermalReading] = []
        for cid in chassis_ids:
            log.debug("[%s] GET /redfish/v1/Chassis/%s/Thermal", self.name, cid)
            try:
                data = self._get(f"/redfish/v1/Chassis/{cid}/Thermal")
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 400, 405, 501):
                    # Chassis exists but has no thermal endpoint — skip silently.
                    log.debug("[%s] chassis %s has no Thermal endpoint (HTTP %d) — skipping",
                              self.name, cid, exc.code)
                    continue
                readings += self._errs(f"Redfish {cid}/Thermal HTTP {exc.code}: {exc}")
                continue
            except TimeoutError:
                log.debug("[%s] chassis %s /Thermal timed out — skipping", self.name, cid)
                continue
            except Exception as exc:
                if "timed out" in str(exc).lower():
                    log.debug("[%s] chassis %s /Thermal timed out — skipping", self.name, cid)
                    continue
                readings += self._errs(f"Redfish {cid}/Thermal: {exc}")
                continue

            raw_temps = data.get("Temperatures", [])
            log.debug("[%s] chassis %s Thermal: %d sensor(s) in response", self.name, cid, len(raw_temps))
            for temp in raw_temps:
                state = (temp.get("Status", {}).get("State") or "").strip().upper()
                if state and state not in ("ENABLED", "ENABLE"):
                    log.debug("[%s] sensor %r skipped (state=%s)", self.name, temp.get("Name"), state)
                    continue
                try:
                    value = float(temp["ReadingCelsius"])
                except (KeyError, TypeError, ValueError):
                    log.debug("[%s] sensor %r skipped (no ReadingCelsius)", self.name, temp.get("Name"))
                    continue
                # Drop NaN / Inf — some BMCs emit these for unpopulated sensors,
                # and NaN >= threshold is always False (would silently report OK).
                if not math.isfinite(value):
                    log.debug("[%s] sensor %r skipped (non-finite reading %r)",
                              self.name, temp.get("Name"), temp.get("ReadingCelsius"))
                    continue

                sensor_name = temp.get("Name", "unknown")
                if self.sensors_filter and not any(
                    f.lower() in sensor_name.lower() for f in self.sensors_filter
                ):
                    log.debug("[%s] sensor %r filtered out (filter=%s)", self.name, sensor_name, self.sensors_filter)
                    continue

                warn, crit = self.warn, self.crit
                if self.use_redfish_thresholds:
                    unc = temp.get("UpperThresholdNonCritical")
                    ucr = temp.get("UpperThresholdCritical")
                    unc_applied = False
                    if unc is not None:
                        try:
                            v = float(unc)
                            if 0 < v < 300:
                                warn = v
                                unc_applied = True
                        except (TypeError, ValueError):
                            pass
                    if ucr is not None:
                        try:
                            v = float(ucr)
                            if 0 < v < 300:
                                crit = v
                        except (TypeError, ValueError):
                            pass
                    # UCR present but UNC absent: derive warn = crit - 15,
                    # but never below the config warn (which acts as a floor).
                    # Rationale: for ambient/inlet sensors vendors set a
                    # conservative UCR (e.g. 42 °C), so UCR-15 = 27 °C can be
                    # uncomfortably close to normal room temperature.  The config
                    # warn (default 30 °C) provides a meaningful minimum headroom
                    # above typical operating conditions.  For hotter component
                    # sensors (UCR ≥ 75 °C) UCR-15 always exceeds the floor and
                    # dominates, so the floor has no effect there.
                    if not unc_applied and crit != self.crit:
                        warn = max(self.warn, crit - 15)

                readings.append(self._r(sensor_name, value, warn=warn, crit=crit))

        log.debug("[%s] got %d reading(s)", self.name, len(readings))
        return readings or self._errs("no temperature sensors in Redfish response")
