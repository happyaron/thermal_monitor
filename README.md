# thermal_monitor

Equipment-room thermal monitoring with pluggable sources and WeCom alerting.

Polls temperatures from heterogeneous infrastructure (bare-metal Linux hosts,
BMCs via IPMI / Redfish, SNMP-capable network gear), writes readings to a
SQLite history, publishes JSON for a single-file web dashboard, and fires
WeCom (企业微信) alerts when configurable thresholds are breached.

## Quick start

Pick one of:

```sh
# Option A — scripted venv setup (recommended).  Creates ./venv/, installs
# thermal_monitor, and also installs ../weixin_work if it's next to this
# tree.  run_monitor.sh auto-detects the venv on subsequent runs.
./setup_venv.sh                     # or: PYTHON=python3.11 ./setup_venv.sh
```

```sh
# Option B — manual install into the ambient Python.
pip install pyyaml
pip install -e ../weixin_work       # for WeCom alert delivery (optional)
```

Then configure and run:

```sh
cp thermal_monitor_example.yaml thermal_monitor.yaml
# edit to match your hardware, credentials, thresholds

./run_monitor.sh -c thermal_monitor.yaml                 # one-shot collect + print
./run_monitor.sh -c thermal_monitor.yaml -i 60           # poll every 60 s
./run_monitor.sh -c thermal_monitor.yaml --dry-run -v    # preview alerts, no send
```

## Source types

| Type            | Protocol                         | External dependency               |
|-----------------|----------------------------------|-----------------------------------|
| `local_sensors` | `sensors -j` on localhost        | `lm-sensors`                      |
| `ssh_sensors`   | SSH + `sensors -j` on remote     | `openssh-client`, lm-sensors remote |
| `ipmi`          | `ipmitool sensor list`           | `ipmitool`                        |
| `redfish`       | Redfish REST (HTTPS + Basic)     | none (stdlib)                     |
| `snmp`          | `snmpget` against an OID list    | `net-snmp` / `snmp`               |

Every source is configured in YAML. See `thermal_monitor_example.yaml` for a
commented walkthrough of every knob. Sequential ranges of identically
configured hosts can be expanded inline via `host_range: "10.0.0.10-35"` —
one entry becomes N sources, one per last octet.

## Execution modes

| Flag                     | Effect                                                                 |
|--------------------------|------------------------------------------------------------------------|
| *(default)*              | Collect once, print ANSI table, send alerts, exit                      |
| `-i N`                   | Poll every N seconds; SIGTERM-safe                                      |
| `--json FILE`            | Also write current readings as JSON (feeds the web dashboard)          |
| `--json`                 | Write JSON to stdout, skip the table (pipe-friendly)                   |
| `--dry-run`              | Preview WeCom markdown in the terminal; nothing sent                    |
| `--log-format plain`     | Log-line output to stderr (timestamp + level + message)                |
| `--log-format systemd`   | Same as plain, plus sd-daemon `<N>` priority prefixes for journald     |
| `-v`                     | DEBUG-level logging                                                     |

## Web dashboard

`thermal_monitor.html` is a self-contained, single-file dashboard that
fetches the `--json` output file every 60 s and renders a sortable table
with group collapsing and auto-expand for non-OK sources. Serve it with any
static HTTP server alongside the JSON:

```sh
python3 -m http.server 8000
# then open http://host:8000/thermal_monitor.html?json=readings.json
```

## systemd deployment

A oneshot service + timer pair is shipped under `systemd/`:

```sh
sudo install -m 644 systemd/thermal-monitor.service /etc/systemd/system/
sudo install -m 644 systemd/thermal-monitor.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now thermal-monitor.timer

journalctl -u thermal-monitor.service -f             # live log
journalctl -u thermal-monitor.service -p warning     # WARN/CRIT only
```

The service uses `DynamicUser=yes`, so no `useradd` step is required —
systemd allocates a transient UID per invocation, and `StateDirectory=`
files persist across restarts and reboots (with their ownership remapped
automatically when needed). The unit runs `run_monitor.sh` with
`--log-format=systemd --json …` under tight sandboxing (`ProtectSystem=strict`,
`ProtectHome=yes`, `PrivateTmp=yes`, `NoNewPrivileges=yes`, plus the
`Protect*` / `Restrict*` family). Tunable paths (`CONFIG`, `JSON_OUT`,
`THERMAL_MONITOR_STATE_FILE`) are exposed via `Environment=` — override with
`systemctl edit thermal-monitor.service` without touching the shipped file.

The timer fires `OnBootSec=1min`, then `OnUnitActiveSec=5min`, with
`RandomizedDelaySec=5s` (staggers load when multiple monitors share a
management VLAN) and `Persistent=true` (catches up after downtime).
Tighten the cadence to 1min for fast-response use cases, or loosen it
to 15min / 30min / 1h for cooler rooms — `systemctl edit thermal-monitor.timer`
lets you override `OnUnitActiveSec=` without editing the shipped file.

## Alerts

WARN and CRIT readings are sent to WeCom via `weixin_work` — either a
group-chat webhook (simplest) or the app API (targets specific
users/departments/tags). Each sensor has an independent cooldown (default
5 min) so a persistent fault doesn't spam the chat; on CRIT, an optional
`@all` mention is appended. Cooldown state is persisted across restarts so
the cooldown survives service restarts and hosts rebooting.

Configure alerting in the `alerting:` section of the YAML; see the example
file for webhook-mode and app-mode snippets.

## Design & tests

- `THERMAL_MONITOR_DESIGN.md` — architecture, data model, source plugin
  contract, threshold-resolution precedence, and known limitations.
- `tests/` — 200 pytest cases covering config parsing, all five source
  plugins, threshold resolution, alerting cooldown behavior (including
  failure paths), atomic writes, display formatting, and the service-mode
  logging output.

Run tests from the repo root:

```sh
PYTHONPATH=. python -m pytest            # without a pip install
pip install -e . && pytest               # once installed
```

## Dependencies

- Python ≥ 3.8
- `pyyaml` — required; parses the config file
- `weixin_work` — optional; only needed for WeCom alert delivery
- System tools per source type in use: `lm-sensors`, `openssh-client`,
  `ipmitool`, `net-snmp` / `snmp`
