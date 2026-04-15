# Thermal Monitor — Design Documentation

## Overview

`thermal_monitor.py` is an equipment-room thermal monitoring tool that collects temperature readings from heterogeneous infrastructure (servers, switches, routers, and the local machine), displays a live status table in the terminal, logs historical data to SQLite, exports JSON snapshots for a web dashboard, and fires WeCom (企业微信) alerts when configurable thresholds are breached.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          thermal_monitor.py                         │
│                                                                     │
│  ┌──────────────┐   ┌────────────────┐   ┌───────────────────────┐ │
│  │  YAML Config  │──▶│  Config Loader  │──▶│  Source Instances     │ │
│  │  (defaults +  │   │  (host_range   │   │  (LocalSensors, SSH, │ │
│  │   sources +   │   │   expansion,   │   │   IPMI, Redfish,     │ │
│  │   alerting)   │   │   merging)     │   │   SNMP)              │ │
│  └──────────────┘   └────────────────┘   └──────────┬────────────┘ │
│                                                      │              │
│                                          ┌───────────▼───────────┐ │
│                                          │  ThreadPoolExecutor   │ │
│                                          │  (parallel I/O)       │ │
│                                          └───────────┬───────────┘ │
│                                                      │              │
│                                          ┌───────────▼───────────┐ │
│                                          │  List[ThermalReading] │ │
│                                          └──┬──────┬──────┬──────┘ │
│                                             │      │      │        │
│                         ┌───────────────────┘      │      └──────┐ │
│                         ▼                          ▼             ▼ │
│                  ┌─────────────┐         ┌──────────────┐  ┌─────┐│
│                  │ Terminal    │         │ Alert Engine │  │ JSON ││
│                  │ Table       │         │ (WeCom via   │  │ File ││
│                  │ (ANSI)      │         │ weixin_work) │  │      ││
│                  └─────────────┘         └──────────────┘  └──┬──┘│
│                                                               │    │
│                  ┌─────────────┐                              │    │
│                  │ SQLite Log  │         ┌────────────────────▼──┐ │
│                  │ (readings   │         │ thermal_monitor.html  │ │
│                  │  history)   │         │ (static dashboard,    │ │
│                  └─────────────┘         │  reads JSON via fetch)│ │
│                                          └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

## Core Data Model

### ThermalReading (dataclass)

The central data unit flowing through the entire pipeline:

| Field    | Type             | Description                                       |
|----------|------------------|---------------------------------------------------|
| `source` | `str`            | Display name of the collection source              |
| `sensor` | `str`            | Sensor label within the source                     |
| `value`  | `float`          | Temperature in °C (0.0 when `error` is set)        |
| `warn`   | `float`          | Warning threshold °C                               |
| `crit`   | `float`          | Critical threshold °C                              |
| `error`  | `Optional[str]`  | Non-None when collection failed                    |

Derived properties:
- `status` — One of `OK`, `WARN`, `CRIT`, `ERROR`, computed from value vs thresholds.
- `alert_key` — `"{source}::{sensor}"`, used for cooldown deduplication.

## Source Plugin System

All sources inherit from `ThermalSource` (ABC) and implement `collect() -> List[ThermalReading]`. The contract: `collect()` must never raise — errors are returned as `ThermalReading` objects with the `error` field set.

### Source Types

| Type             | Class                | Protocol              | External Dependency       |
|------------------|----------------------|-----------------------|---------------------------|
| `local_sensors`  | `LocalSensorsSource` | `sensors -j` (local)  | lm-sensors                |
| `ssh_sensors`    | `SSHSensorsSource`   | SSH + `sensors -j`    | ssh, lm-sensors on remote |
| `ipmi`           | `IPMISource`         | `ipmitool sensor list` | ipmitool                 |
| `redfish`        | `RedfishSource`      | Redfish REST API      | None (stdlib urllib)       |
| `snmp`           | `SNMPSource`         | `snmpget` CLI         | net-snmp                  |

### Source Hierarchy

```
ThermalSource (ABC)
├── LocalSensorsSource
│   └── SSHSensorsSource  (inherits parsing, overrides collect with SSH transport)
├── IPMISource
├── RedfishSource
└── SNMPSource
```

`SSHSensorsSource` extends `LocalSensorsSource` to reuse `_parse_sensors_json()`, only replacing the data-acquisition step (SSH subprocess instead of local subprocess).

### Threshold Resolution

Thresholds are resolved in a layered priority system (highest priority first):

1. **`sensor_thresholds`** config overrides (exact name match, then longest substring match)
2. **Protocol-reported thresholds** (IPMI SDR UNC/UCR, Redfish UpperThreshold*)
3. **Per-source `warn`/`crit`** in YAML
4. **`defaults` section** `warn`/`crit`
5. **Class-level defaults** (hardcoded in each source `__init__`)

### Primary Sensor Selection

The "primary sensor" determines the representative ambient temperature displayed in the summary column. Selection modes:

- **`auto`** (default) — Tiered heuristic:
  - T0: Pure "Inlet" sensors (excluding component-qualified names like "P/S 1 Inlet")
  - T1: Pure "Ambient" sensors (same exclusions)
  - T2: Positional keywords (inlet, ambient, front, board, system)
  - T3: Any remaining non-excluded sensor
  - Within each tier: lowest reading wins (closest to true room temperature)
- **Exact string** — Match sensor name exactly
- **Ordered substring list** — Try each pattern in order, first match wins

## Configuration System

### YAML Structure

```yaml
settings:          # Runtime tuning (max_workers, jitter)
defaults:          # Global fallback values for all sources
  sensor_thresholds:  # Pattern-based threshold overrides
sources:           # List of source definitions
  - name: "..."
    type: local_sensors | ssh_sensors | ipmi | redfish | snmp
    host_range: "prefix.start-end"  # Optional: expands to N sources
    ...
alerting:          # WeCom delivery config (webhook or app mode)
logging:           # SQLite reading log config
```

### host_range Expansion

A single source block with `host_range: "192.168.10.31-35"` expands into 5 independent source instances at config load time, each with:
- Unique host IP
- Name resolved from template placeholders: `{}` (display index), `{ip}` (last octet), `{host}` (full IP)
- Optional `start_index` offset for display numbering
- Optional `enable_hosts` list to selectively enable specific hosts

Expanded sources share a `group` attribute derived from the name template prefix, enabling collapsed group display in the terminal and dashboard.

### Config Merging

Per-source values override `defaults`. `sensor_thresholds` is deep-merged (both global patterns and per-source exact names apply; per-source keys win on collision).

## Collection Pipeline

```
1. load_config(yaml_path)
   → List[ThermalSource], alerting_cfg, settings, logging_cfg

2. collect_all(sources, max_workers, jitter)
   → ThreadPoolExecutor submits _collect_one() per source
   → Each worker: optional jitter delay → src.collect() → apply sensor_thresholds
   → Aggregate all List[ThermalReading] into flat list

3. Output phase (all operate on the same readings list):
   a. print_table()      — ANSI terminal display
   b. readings_to_dict() — JSON serialization → file or stdout
   c. _write_log()       — SQLite INSERT + retention pruning
   d. send_alerts()      — WeCom notification (subject to cooldown)
```

### Concurrency Model

- **ThreadPoolExecutor** with `max_workers` threads (default: one per source).
- Each source is I/O-bound (subprocess call or HTTP request), so threading provides near-linear speedup.
- Optional **jitter**: each worker sleeps `uniform(0, jitter)` seconds before starting, spreading burst load on shared management networks.

## Alerting System

### Delivery Modes

| Mode      | Client          | Targeting                                    |
|-----------|-----------------|----------------------------------------------|
| `webhook` | `WebhookClient` | Group chat (implicit from webhook key)        |
| `app`     | `AppClient`     | `to_user` / `to_party` / `to_tag` (explicit) |

### Alert Flow

1. Filter readings to `WARN` or `CRIT` status.
2. Apply per-sensor cooldown: skip sensors alerted within `alert_cooldown` seconds.
3. Build WeCom Markdown message (CRIT items first, then WARN).
4. If any CRIT and `mention_all_on_crit` is true: send a second plain-text `@all` message.
5. Update cooldown state (persisted to `state_file` as JSON).

### Cooldown State

- Stored as `{alert_key: last_alert_timestamp}` in a JSON file (default: `/tmp/thermal_monitor_state.json`).
- Survives script restarts.
- Updated regardless of `--dry-run` to prevent terminal spam.

## Terminal Display

The terminal table uses ANSI escape codes (auto-disabled when stdout is not a TTY) with a compact per-source summary:

- **TEMP column**: Primary inlet sensor value (ambient representative).
- **STATUS column**: Worst status across all sensors for the source.
- **Alert hint**: Inline note when the alerting sensor differs from the primary.
- **Group collapsing**: OK groups collapse to a single header line; non-OK groups auto-expand to show member rows with sensor-level detail.
- **Dry-run preview**: WeCom Markdown is rendered to terminal-approximated ANSI output.

## JSON Output & Web Dashboard

### JSON Schema (readings.json)

```json
{
  "timestamp": "2026-04-11T12:00:00Z",
  "sources": [
    {
      "name": "Source Name",
      "status": "OK|WARN|CRIT|ERROR",
      "max_temp": 28.5,
      "primary_temp": 24.3,
      "primary_warn": 30.0,
      "primary_crit": 38.0,
      "alert_hint": null,
      "group": "Group Name",
      "short_name": "Short Name",
      "sensors": [
        {
          "name": "Sensor Name",
          "value": 24.3,
          "warn": 30.0,
          "crit": 38.0,
          "status": "OK"
        }
      ]
    }
  ],
  "summary": {
    "total_sources": 10,
    "total_sensors": 45,
    "ok": 43,
    "warn": 1,
    "crit": 1,
    "error": 0
  }
}
```

### HTML Dashboard (thermal_monitor.html)

A self-contained single-file dashboard that:
- Fetches `readings.json` via `fetch()` on a configurable interval (default: 60s).
- Renders a sortable table with the same group collapse/expand semantics as the terminal.
- Auto-expands non-OK sources and groups.
- Supports URL query parameters: `?refresh=N&json=path`.
- Uses dark theme with monospace typography; responsive layout for mobile.

Intended deployment: serve the HTML and JSON file via any static HTTP server (e.g., `python -m http.server`), with the monitor script writing JSON to the same directory.

## SQLite Reading Log

### Schema

```sql
CREATE TABLE readings (
    ts      TEXT NOT NULL,    -- ISO 8601 UTC timestamp
    source  TEXT NOT NULL,
    sensor  TEXT NOT NULL,
    value   REAL NOT NULL,
    warn    REAL NOT NULL,
    crit    REAL NOT NULL,
    status  TEXT NOT NULL
);
CREATE INDEX readings_ts ON readings(ts);
```

- Error-sentinel readings (collection failures) are excluded from logging.
- Retention: automatic pruning of rows older than `retention_days` (default: 30) on each write.

## Execution Modes

| Mode | Command | Behavior |
|------|---------|----------|
| One-shot | `python thermal_monitor.py -c config.yaml` | Collect once, print, alert, exit |
| Polling | `python thermal_monitor.py -c config.yaml -i 60` | Repeat every N seconds |
| Dry-run | `python thermal_monitor.py -c config.yaml --dry-run` | No WeCom send; preview alerts in terminal |
| JSON stdout | `python thermal_monitor.py -c config.yaml --json` | JSON to stdout (no table) |
| JSON file | `python thermal_monitor.py -c config.yaml --json readings.json` | JSON to file + terminal table |
| Verbose | `python thermal_monitor.py -c config.yaml -v` | Debug logging enabled |
| Plain log | `python thermal_monitor.py -c config.yaml --log-format plain` | Structured log lines (timestamp + level) on stderr instead of the ANSI table |
| Systemd | `python thermal_monitor.py -c config.yaml --log-format systemd` | Plain log lines with sd-daemon `<N>` priority prefixes for journald |

### Service / daemon mode

`--log-format {plain,systemd}` swaps the interactive ANSI table for
line-oriented log output on stderr via Python's `logging` module, so the
script behaves correctly under a process supervisor:

- One `INFO` heartbeat per collection cycle: `cycle ok=… warn=… crit=… err=… sources=… sensors=…`
- One `WARNING` line per WARN reading, one `CRITICAL` per CRIT, one `ERROR` per collection failure.
- `systemd` format prefixes each line with an `sd-daemon(3)` priority code
  (`<2>` crit, `<3>` err, `<4>` warn, `<6>` info) so `systemd-journald`
  classifies severity correctly and operators can filter with e.g.
  `journalctl -u thermal-monitor -p warning`.
- JSON output, web-dashboard writes, the SQLite reading log, and WeCom
  alert delivery are unchanged; they run as before in every format.
- Intended shape of the deployment:
  - A **oneshot service** (`systemd/thermal-monitor.service`) runs one
    collection cycle with `--log-format=systemd --json …` and exits.
    `DynamicUser=yes` allocates a transient UID per invocation, so no
    service-user setup is needed; `StateDirectory=thermal_monitor`
    persists `state.json` / `readings.db` / `readings.json` across runs
    (systemd remaps ownership to the current dynamic UID before each
    start);
  - a **timer** (`systemd/thermal-monitor.timer`) re-triggers it every
    15 minutes with `OnBootSec=1min` / `OnUnitActiveSec=15min`,
    `RandomizedDelaySec=5s`, and `Persistent=true` for catch-up after
    downtime — tighten or loosen the cadence via `systemctl edit`;
  - the JSON file is served by any static HTTP server alongside
    `thermal_monitor.html`;
  - WeCom receives throttled alerts for WARN/CRIT;
  - journald captures every WARN/CRIT observation (independent of the
    alert cooldown) and the SQLite DB retains `retention_days` of history.
- Install by copying the two unit files to `/etc/systemd/system/`, then
  `systemctl enable --now thermal-monitor.timer`.  See the header comments
  in each unit file for user, paths, state-directory, and sandboxing notes.
  To drive the service as a long-running process instead (`Type=simple`
  with `-i 60`), drop the timer and swap `Type=oneshot` for `Type=simple`
  in a drop-in — both patterns are supported by the `--log-format=systemd`
  output, but oneshot+timer gives better per-cycle observability.

## Dependencies

| Package | Required | Purpose |
|---------|----------|---------|
| `pyyaml` | Yes | Config file parsing |
| `weixin_work` | Optional | WeCom alert delivery (this repo) |
| `requests` | Via weixin_work | HTTP client for WeCom API |
| `lm-sensors` | For local/SSH sources | `sensors -j` command |
| `ipmitool` | For IPMI sources | `ipmitool sensor list` command |
| `net-snmp` | For SNMP sources | `snmpget` command |
| `ssh` | For SSH sources | OpenSSH client |

Python standard library modules used: `argparse`, `concurrent.futures`, `json`, `logging`, `sqlite3`, `ssl`, `subprocess`, `urllib.request`.

## Known Issues and Limitations

1. **Monolithic single file** (~1890 lines) — should be refactored into a package with separate modules for sources, display, alerting, and config.
2. **No SIGTERM handling** — polling mode catches `KeyboardInterrupt` but not SIGTERM, which is what `systemd stop` / `docker stop` sends.
3. **IPMI/SNMP passwords visible in process list** — `ipmitool -P` and `snmpget -c` expose credentials via `ps`. Should use `-E`/`-f` alternatives.
4. **SQLite timestamp format mismatch** — INSERT uses ISO 8601 (`T` separator, `Z` suffix) while DELETE pruning uses SQLite `datetime()` (space separator). Works in practice for dates not at the exact retention boundary, but is technically inconsistent.
5. **Dashboard stale-data indicator not wired** — CSS defines `.dot.stale` but the JavaScript never applies it; the dashboard shows "connected" even when JSON data is hours old.
6. **No authentication on dashboard** — the HTML reads a local JSON file with no access control.
7. **Redfish SSL context re-created per HTTP request** — should be cached on the `RedfishSource` instance.
8. **SSH `StrictHostKeyChecking=no`** — disables host key verification; acceptable for isolated management networks but should be documented as a security trade-off.
