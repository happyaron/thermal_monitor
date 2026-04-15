from __future__ import annotations
import logging
import re
import sys
from typing import Dict, List, Optional, Tuple, Union
from thermal_monitor.sources.base import ThermalSource
from thermal_monitor.sources.registry import SOURCE_TYPES

log = logging.getLogger(__name__)


# Config keys whose values get interpolated into argv for ssh / snmpget /
# ipmitool.  A value beginning with "-" would be parsed as an option flag
# (e.g. user "-oProxyCommand=sh_cmd" + host turns `ssh user@host` into
# `ssh -oProxyCommand=sh_cmd@host` → local RCE).  Reject such values at
# config-load time so the source is skipped before the subprocess runs.
_ARGV_UNSAFE_KEYS = ("host", "user", "community", "interface")


def _argv_injection_error(scfg: dict) -> Optional[str]:
    """Return an error message if any argv-destined value starts with '-'."""
    for k in _ARGV_UNSAFE_KEYS:
        v = scfg.get(k)
        if isinstance(v, str) and v.startswith("-"):
            return (
                f"{k}={v!r} starts with '-' — would be parsed as an option "
                f"flag by ssh/snmpget/ipmitool (argv-injection risk)"
            )
    return None


def expand_host_range(scfg: dict) -> List[dict]:
    """
    Expand a source block with ``host_range`` into one dict per host.

    host_range: "192.168.196.12-20"
        Parsed as prefix = "192.168.196", last-octet range 12–20.
        Generates one source per value: .12, .13, …, .20.

    name: "Rack 196.2 - Bay {} ({host})"
        Three optional placeholders, all resolved per host:
          {}      display index — start_index + (octet − first_octet),
                  or the last octet itself when start_index is omitted.
          {ip}    last octet of the host IP (always the raw octet).
          {host}  full host IP address.

        Examples:
          "HW Storage {}"             → "HW Storage 231"
          "Bay {} ({host})"           → "Bay 1 (192.168.196.12)"
          "Bay {} (.{ip})"            → "Bay 1 (.12)"

    enable_hosts: [13, 16, 19]
        Hosts whose last octet is in this list get  enabled: true.
        All others are left unchanged (inherit defaults/per-range setting).
    """
    raw_range = scfg["host_range"]
    last_dot = raw_range.rfind(".")
    if last_dot < 0:
        raise ValueError(f"host_range {raw_range!r}: expected 'prefix.start-end' format")
    prefix = raw_range[:last_dot]
    tail   = raw_range[last_dot + 1:]
    if "-" not in tail:
        raise ValueError(f"host_range {raw_range!r}: missing '-' in last-octet range")
    lo_str, hi_str = tail.split("-", 1)
    lo, hi = int(lo_str), int(hi_str)
    if lo > hi:
        raise ValueError(f"host_range {raw_range!r}: start > end")

    start_index  = scfg.get("start_index")          # None → use last octet
    enable_hosts = set(scfg.get("enable_hosts", []))
    name_tmpl    = scfg.get("name", "Host {}")

    # Derive group name from text before the first placeholder ({}, {ip}, {host}).
    # If the prefix contains ' - ', split there (e.g. "Rack 196.2 - Bay "
    # → group "Rack 196.2", keeping "Bay" for the per-member short name).
    _first_ph = re.search(r'\{(?:ip|host)?\}', name_tmpl)
    if _first_ph:
        _prefix = name_tmpl[:_first_ph.start()].rstrip()
        _sep = _prefix.rfind(" - ")
        group_name = _prefix[:_sep] if _sep > 0 else _prefix.rstrip(" -(")
    else:
        group_name = None

    # Strip host_range-specific keys from the per-source base.
    base = {k: v for k, v in scfg.items()
            if k not in ("host_range", "start_index", "enable_hosts")}
    if group_name:
        base["_group"] = group_name

    result = []
    for octet in range(lo, hi + 1):
        offset      = octet - lo
        display_idx = (start_index + offset) if start_index is not None else octet
        entry       = dict(base)
        entry["host"] = f"{prefix}.{octet}"
        full_host = f"{prefix}.{octet}"
        entry["name"] = (name_tmpl
                         .replace("{}", str(display_idx), 1)
                         .replace("{ip}", str(octet))
                         .replace("{host}", full_host))
        if enable_hosts and octet in enable_hosts:
            entry["enabled"] = True
        result.append(entry)
    return result


def load_config(path: str) -> Tuple[List[ThermalSource], dict, dict, dict]:
    """
    Parse a YAML config file and return (sources, alerting_cfg).
    Exits with a clear error message if pyyaml is missing.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        print(
            "ERROR: pyyaml is required for config parsing.\n"
            "       Install it with:  pip install pyyaml",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(path) as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        print(f"ERROR: {path} is not a valid YAML mapping.", file=sys.stderr)
        sys.exit(1)

    # Expand host_range blocks into individual source dicts before processing.
    raw_sources = []
    for scfg in raw.get("sources", []):
        if "host_range" in scfg:
            try:
                raw_sources.extend(expand_host_range(scfg))
            except ValueError as exc:
                log.warning("host_range expansion error — skipping block: %s", exc)
        else:
            raw_sources.append(scfg)

    defaults: dict = raw.get("defaults", {})
    sources: List[ThermalSource] = []
    for i, scfg in enumerate(raw_sources):
        merged = {**defaults, **scfg}   # source-level keys override global defaults
        # sensor_thresholds: deep-merge so global patterns and per-source exact
        # names both apply.  Per-source keys win over same-named default keys.
        merged["sensor_thresholds"] = {
            **defaults.get("sensor_thresholds", {}),
            **scfg.get("sensor_thresholds", {}),
        }
        if not merged.get("enabled", True):
            log.debug("Source #%d (%r) is disabled — skipping", i, merged.get("name", "?"))
            continue
        if not merged.get("name"):
            # Name drives by_source grouping, alert_key, and display output —
            # sources without a name would silently merge into an "" bucket.
            log.warning("Source #%d: missing 'name' — skipping", i)
            continue
        stype = merged.get("type")
        cls = SOURCE_TYPES.get(stype)
        if cls is None:
            log.warning("Source #%d: unknown type %r — skipping", i, stype)
            continue
        argv_err = _argv_injection_error(merged)
        if argv_err:
            log.warning("Source #%d (%r): %s — skipping",
                        i, merged.get("name", "?"), argv_err)
            continue
        try:
            src = cls(merged)
            src.sensor_thresholds = merged.get("sensor_thresholds", {})
            src.group = merged.get("_group")
            ps = merged.get("primary_sensor")
            if ps is not None and ps != "auto":
                src.primary_sensor = ps
            sources.append(src)
        except Exception as exc:
            log.warning("Source #%d (%r): init error — %s", i, stype, exc)

    # Detect duplicate names — these would silently merge readings under one
    # bucket in by_source and share cooldown state via alert_key.
    seen: Dict[str, int] = {}
    for src in sources:
        seen[src.name] = seen.get(src.name, 0) + 1
    dupes = sorted(n for n, count in seen.items() if count > 1)
    if dupes:
        log.warning(
            "Duplicate source name(s) in config: %s — readings will be "
            "merged under one key and WeCom cooldown will be shared",
            dupes,
        )

    alerting_cfg: dict = raw.get("alerting", {})
    settings: dict     = raw.get("settings", {})
    logging_cfg: dict  = raw.get("logging", {})
    return sources, alerting_cfg, settings, logging_cfg
