from __future__ import annotations
import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Union
from thermal_monitor.config import load_config
from thermal_monitor.collector import collect_all
from thermal_monitor.display import print_table
from thermal_monitor.alerts import send_alerts, _load_state, _save_state
from thermal_monitor.serialization import readings_to_dict
from thermal_monitor.logging_db import _open_log_db, _write_log
from thermal_monitor.sources.base import ThermalSource

log = logging.getLogger(__name__)

DEFAULT_CONFIG = "thermal_monitor.yaml"


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="thermal_monitor",
        description=(
            "Equipment-room thermal monitoring. "
            "Collects temperatures from servers/switches/routers via multiple "
            "protocols and alerts via WeCom when thresholds are breached."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python thermal_monitor.py -c thermal_monitor.yaml
  python thermal_monitor.py -c thermal_monitor.yaml -i 60
  python thermal_monitor.py -c thermal_monitor.yaml --dry-run -v
  python thermal_monitor.py -c thermal_monitor.yaml --json              # JSON to stdout
  python thermal_monitor.py -c thermal_monitor.yaml --json readings.json  # JSON to file + table
        """,
    )
    parser.add_argument(
        "-c", "--config",
        default=DEFAULT_CONFIG,
        metavar="FILE",
        help=f"YAML config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "-i", "--interval",
        type=int, default=0,
        metavar="SECONDS",
        help="Polling interval in seconds; 0 = run once and exit (default: 0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be alerted but do not send WeCom messages",
    )
    parser.add_argument(
        "--json",
        nargs="?",
        const="-",
        default=None,
        metavar="FILE",
        help="Write readings as JSON (default: stdout; give a path to write to file)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s  %(name)s: %(message)s",
    )

    if not Path(args.config).exists():
        print(
            f"Config file not found: {args.config}\n"
            f"Copy thermal_monitor_example.yaml and edit it for your environment.",
            file=sys.stderr,
        )
        sys.exit(1)

    sources, alerting_cfg, settings, logging_cfg = load_config(args.config)
    if not sources:
        print("No sources configured — check your YAML.", file=sys.stderr)
        sys.exit(1)

    # Mapping of source name → group name for host_range-expanded sources.
    source_groups = {src.name: src.group for src in sources if src.group}
    # Mapping of source name → primary_sensor config override.
    primary_sensors = {src.name: src.primary_sensor for src in sources if src.primary_sensor}

    max_workers = int(settings.get("max_workers", 0))
    jitter      = float(settings.get("jitter", 0.0))
    state_file  = alerting_cfg.get("state_file", "/tmp/thermal_monitor_state.json")
    state       = _load_state(state_file)

    log_conn: Optional[sqlite3.Connection] = None
    log_db   = logging_cfg.get("db_file")
    if log_db:
        try:
            log_conn = _open_log_db(log_db)
            log.debug("log: opened %s", log_db)
        except Exception as exc:
            print(f"WARNING: could not open log DB {log_db!r}: {exc}", file=sys.stderr)

    log_retention = int(logging_cfg.get("retention_days", 30))

    json_to_stdout = (args.json == "-")

    def run_once() -> None:
        readings = collect_all(sources, max_workers=max_workers, jitter=jitter)

        # Output: JSON and/or terminal table.
        if args.json is not None:
            json_str = json.dumps(
                readings_to_dict(readings, source_groups, primary_sensors),
                indent=2, ensure_ascii=False,
            )
            if json_to_stdout:
                print(json_str)
            else:
                Path(args.json).write_text(json_str + "\n")
                log.info("JSON written to %s", args.json)
        if not json_to_stdout:
            print_table(readings, source_groups, primary_sensors)

        if log_conn is not None and not args.dry_run:
            try:
                _write_log(log_conn, readings, log_retention)
            except Exception as exc:
                log.warning("log: write failed: %s", exc)
        send_alerts(readings, alerting_cfg, state, time.time(),
                    dry_run=args.dry_run)
        _save_state(state_file, state)

    _running = [True]

    def _on_stop(signum, frame):   # noqa: ANN001
        _running[0] = False

    signal.signal(signal.SIGTERM, _on_stop)

    try:
        if args.interval > 0:
            print(
                f"Polling every {args.interval} s — Ctrl-C to stop.",
                file=sys.stderr,
            )
            try:
                while _running[0]:
                    run_once()
                    # Sleep in 1-s ticks so SIGTERM is detected within 1 s.
                    for _ in range(args.interval):
                        if not _running[0]:
                            break
                        time.sleep(1)
            except KeyboardInterrupt:
                pass
            print("\nStopped.", file=sys.stderr)
        else:
            run_once()
    finally:
        if log_conn is not None:
            log_conn.close()
