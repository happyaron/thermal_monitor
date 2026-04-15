#!/bin/bash
# run_monitor.sh — invoke thermal_monitor as `python -m`.
#
# Resolves the script's real location (follows symlinks) so the wrapper
# keeps working when dropped into /usr/local/bin — or any other PATH entry —
# as a symlink back to the install tree.
#
# Two layouts are supported automatically:
#
#   1. Production: a venv at <ROOT>/venv/, with thermal_monitor and
#      weixin_work pip-installed into it.  The venv's Python is used
#      directly; deps and both packages are resolved from site-packages.
#
#   2. Source tree / dev: no venv.  Falls back to the system Python and
#      puts <ROOT> on PYTHONPATH so a sibling thermal_monitor/ and
#      weixin_work/ (as uninstalled packages) are importable.  Requires
#      pyyaml (and requests, for alerting) on the system Python.
#
# The systemd unit at systemd/thermal-monitor.service invokes this script.
#
# Usage:
#   run_monitor.sh -c thermal_monitor.yaml
#   run_monitor.sh -c /etc/thermal_monitor/thermal_monitor.yaml -i 60 \
#                  --json /var/lib/thermal_monitor/readings.json \
#                  --log-format systemd

# readlink -f yields an absolute, canonical path with every symlink resolved,
# so ROOT is the real install directory regardless of how the script was
# invoked (direct path, relative path, or via a symlink in /usr/local/bin).
ROOT="$(dirname "$(readlink -f "$0")")"

# Production: venv sitting next to the script — use its interpreter directly.
if [ -x "$ROOT/venv/bin/python3" ]; then
    exec "$ROOT/venv/bin/python3" -m thermal_monitor "$@"
fi

# Source tree / dev: system Python with the repo root on PYTHONPATH.
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m thermal_monitor "$@"
