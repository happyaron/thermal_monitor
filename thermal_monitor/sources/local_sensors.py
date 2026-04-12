from __future__ import annotations
import json
import logging
import subprocess
from typing import List, Optional
from thermal_monitor.sources.base import ThermalSource
from thermal_monitor.models import ThermalReading

log = logging.getLogger(__name__)


class LocalSensorsSource(ThermalSource):
    """
    Read from ``sensors -j``  (lm-sensors package).

    Config keys:
        chips   — list of chip-name substrings to include (optional)
        labels  — list of feature-label substrings to include (optional)
    """

    # Chip-name prefixes that represent component die temps — not useful for
    # monitoring room/ambient conditions.
    _NOISY_CHIPS = (
        "coretemp",   # Intel CPU cores
        "k10temp",    # AMD CPU (Zen/Zen2/Zen3)
        "zenpower",   # AMD CPU alternative driver
        "k8temp",     # older AMD
        "amdgpu",     # AMD GPU die
        "nouveau",    # NVIDIA GPU (open driver)
        "nvidia",     # NVIDIA GPU
        "nvme",       # NVMe SSD
        "spd",        # DDR memory module (SPD temp)
        "drivetemp",  # SATA drive temp
    )
    # Feature-label substrings that are clearly ambient/environmental.
    _AMBIENT_LABELS = (
        "ambient", "intake", "inlet", "exhaust",
        "board", "system", "chassis", "case",
        "pch",    # Platform Controller Hub — reflects board temp
    )

    def __init__(self, cfg: dict):
        super().__init__(
            name=cfg.get("name", "local"),
            warn=float(cfg.get("warn", 75)),
            crit=float(cfg.get("crit", 90)),
        )
        self.filter_chips:   List[str] = cfg.get("chips",   [])
        self.filter_labels:  List[str] = cfg.get("labels",  [])
        self.exclude_chips:  List[str] = cfg.get("exclude_chips",  [])
        self.exclude_labels: List[str] = cfg.get("exclude_labels", [])
        self.ambient_only:   bool      = bool(cfg.get("ambient_only", False))

    def _chip_is_noisy(self, chip: str) -> bool:
        chip_l = chip.lower()
        return any(chip_l.startswith(p) for p in self._NOISY_CHIPS)

    def _label_is_ambient(self, label: str) -> bool:
        label_l = label.lower()
        return any(kw in label_l for kw in self._AMBIENT_LABELS)

    # Exposed so SSHSensorsSource can reuse parsing logic with different JSON.
    def _parse_sensors_json(self, data: dict) -> List[ThermalReading]:
        readings: List[ThermalReading] = []
        skipped_chips: List[str] = []

        for chip, chip_data in data.items():
            chip_l = chip.lower()

            # ── chip-level filtering ──────────────────────────────────────
            if self.filter_chips and not any(f in chip for f in self.filter_chips):
                skipped_chips.append(chip)
                continue
            if self.exclude_chips and any(f in chip for f in self.exclude_chips):
                skipped_chips.append(chip)
                continue
            if self.ambient_only and self._chip_is_noisy(chip):
                skipped_chips.append(chip)
                continue

            for feature, fdata in chip_data.items():
                if feature == "Adapter" or not isinstance(fdata, dict):
                    continue

                feature_l = feature.lower()

                # ── feature-label filtering ───────────────────────────────
                if self.filter_labels and not any(
                    f.lower() in feature_l for f in self.filter_labels
                ):
                    continue
                if self.exclude_labels and any(
                    f.lower() in feature_l for f in self.exclude_labels
                ):
                    continue
                # ambient_only: on kept chips, further restrict to sensors
                # whose label suggests environmental relevance.  acpitz
                # temp1/temp2 are kept unconditionally as they represent
                # chassis/board zones.
                if self.ambient_only and not self._label_is_ambient(feature):
                    if "acpitz" not in chip_l:
                        continue

                for sub, val in fdata.items():
                    # Only accept temperature sub-features (temp*_input).
                    # Fan/voltage/current sensors also use _input — skip them.
                    if not sub.startswith("temp") or not sub.endswith("_input"):
                        continue
                    if not isinstance(val, (int, float)):
                        continue
                    prefix = sub[: -len("_input")]
                    # Prefer sensor-reported critical/max over config defaults.
                    raw_crit = (fdata.get(f"{prefix}_crit")
                                or fdata.get(f"{prefix}_max"))
                    sensor_crit: Optional[float] = None
                    if raw_crit is not None:
                        fc = float(raw_crit)
                        if 0 < fc < 200:   # sanity check (200 °C max realistic)
                            sensor_crit = fc
                    readings.append(self._r(
                        sensor=f"{chip}/{feature}",
                        value=float(val),
                        crit=sensor_crit,
                    ))

        if skipped_chips:
            log.debug("[%s] skipped chips: %s", self.name, ", ".join(skipped_chips))
        return readings or self._errs("no temperature sensors found in sensors output")

    def collect(self) -> List[ThermalReading]:
        log.debug("[%s] running: sensors -j", self.name)
        try:
            result = subprocess.run(
                ["sensors", "-j"],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            return self._errs("sensors not found — install lm-sensors")
        except subprocess.TimeoutExpired:
            return self._errs("sensors timed out")

        if result.returncode != 0:
            msg = result.stderr.strip()[:120]
            return self._errs(f"sensors exited {result.returncode}: {msg}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return self._errs(f"JSON parse error: {exc}")
        readings = self._parse_sensors_json(data)
        log.debug("[%s] got %d reading(s)", self.name, len(readings))
        return readings
