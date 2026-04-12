from __future__ import annotations
import json
import logging
import os
import subprocess
from typing import List
from thermal_monitor.sources.local_sensors import LocalSensorsSource
from thermal_monitor.models import ThermalReading

log = logging.getLogger(__name__)


class SSHSensorsSource(LocalSensorsSource):
    """
    Run ``sensors -j`` on a remote Linux host over SSH.

    Uses key-based auth (SSH agent or explicit key_file).  For password auth
    install sshpass and add ``ssh_opts: ["-o", "PasswordAuthentication=yes"]``
    then call via sshpass externally.

    Config keys (in addition to LocalSensorsSource):
        host        — IP/hostname  (required)
        user        — SSH user     (default: root)
        port        — SSH port     (default: 22)
        key_file    — path to private key  (optional, uses agent if omitted)
        ssh_opts    — extra SSH option strings as a list  (optional)
    """

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.host     = cfg["host"]
        self.user     = cfg.get("user", "root")
        self.port     = int(cfg.get("port", 22))
        self.key_file = cfg.get("key_file")
        self.ssh_opts: List[str] = cfg.get("ssh_opts", [])

    def collect(self) -> List[ThermalReading]:
        cmd = [
            "ssh",
            "-p", str(self.port),
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
        ]
        if self.key_file:
            cmd += ["-i", os.path.expanduser(self.key_file)]
        cmd += self.ssh_opts
        cmd += [f"{self.user}@{self.host}", "sensors -j"]

        log.debug("[%s] running: %s", self.name, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        except FileNotFoundError:
            return self._errs("ssh not found")
        except subprocess.TimeoutExpired:
            return self._errs(f"SSH to {self.host} timed out")

        if result.returncode != 0:
            msg = result.stderr.strip()[:120]
            return self._errs(f"SSH failed ({result.returncode}): {msg}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return self._errs(f"JSON parse error on remote output: {exc}")
        readings = self._parse_sensors_json(data)
        log.debug("[%s] got %d reading(s)", self.name, len(readings))
        return readings
