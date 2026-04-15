"""
Log-line output for service / daemon mode.

Replaces the interactive ANSI table with structured log lines suitable for
log files, syslog, or systemd journal ingestion.  Two formats are offered:

``plain``
    Standard Python logging output (timestamp + level + message), useful
    when the wrapper redirects stderr to a file or to ``logger(1)``.

``systemd``
    Same content, but each line is prefixed with an sd-daemon ``<N>``
    priority code so ``systemd-journald`` classifies WARN / CRIT lines
    with the correct severity.  See ``man:sd-daemon(3)``.

In either format, every collection cycle emits:

* one INFO heartbeat summarizing the cycle (ok / warn / crit / err counts),
* one WARNING line per WARN reading,
* one ERROR  line per ERROR (collection-failure) reading,
* one CRITICAL line per CRIT reading.

This is independent of the WeCom alert cooldown — the system log records
every observation while the chat notification stays rate-limited.
"""
from __future__ import annotations
import logging
import sys
from typing import Dict, List, Optional, Union
from thermal_monitor.models import ThermalReading

# Logger used for per-cycle status output.  Distinct from module loggers so
# operators can filter it independently in journalctl (e.g. -t status).
log = logging.getLogger("thermal_monitor.status")


# sd-daemon log priority prefixes — see man:sd-daemon(3).
# journald strips the prefix from the message and stores it as PRIORITY=.
SD_PRIORITY = {
    logging.DEBUG:    "<7>",   # debug
    logging.INFO:     "<6>",   # info
    logging.WARNING:  "<4>",   # warning
    logging.ERROR:    "<3>",   # err
    logging.CRITICAL: "<2>",   # crit
}


class SystemdFormatter(logging.Formatter):
    """Prepend sd-daemon ``<N>`` priority codes so journald sees severity."""

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return SD_PRIORITY.get(record.levelno, "<6>") + msg


def configure_log_output(fmt: str, debug: bool = False) -> None:
    """
    Replace the root logger's handlers with a stderr handler suited to *fmt*.

    fmt: ``"plain"`` or ``"systemd"``.
    debug: when true, root level is DEBUG; otherwise INFO so the heartbeat
        and WARN/CRIT lines are emitted but per-source debug noise is hidden.
    """
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "systemd":
        # journald supplies its own timestamp; keep the message lean.
        handler.setFormatter(SystemdFormatter("%(name)s: %(message)s"))
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        ))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)


def emit_status_log(
    readings: List[ThermalReading],
    source_groups: Optional[Dict[str, str]] = None,   # accepted for API parity
    primary_sensors: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> None:
    """
    Emit one INFO heartbeat plus one WARNING / ERROR / CRITICAL line per
    non-OK reading.

    ``source_groups`` and ``primary_sensors`` are accepted so this function
    is a drop-in replacement for ``print_table``; they are unused here
    because the per-sensor lines already carry the source name.
    """
    n_ok   = sum(1 for r in readings if r.status == "OK")
    n_warn = sum(1 for r in readings if r.status == "WARN")
    n_crit = sum(1 for r in readings if r.status == "CRIT")
    n_err  = sum(1 for r in readings if r.status == "ERROR")
    n_src  = len({r.source for r in readings})

    log.info(
        "cycle ok=%d warn=%d crit=%d err=%d sources=%d sensors=%d",
        n_ok, n_warn, n_crit, n_err, n_src, len(readings),
    )

    for r in readings:
        if r.status == "OK":
            continue
        if r.status == "WARN":
            log.warning(
                "%s / %s: %.1f°C >= warn=%.0f°C",
                r.source, r.sensor, r.value, r.warn,
            )
        elif r.status == "CRIT":
            log.critical(
                "%s / %s: %.1f°C >= crit=%.0f°C",
                r.source, r.sensor, r.value, r.crit,
            )
        elif r.status == "ERROR":
            log.error(
                "%s / %s: collection failed: %s",
                r.source, r.sensor, r.error,
            )
