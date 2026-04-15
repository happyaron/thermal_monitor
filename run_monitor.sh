#!/bin/bash
# run_monitor.sh — invoke thermal_monitor as `python -m` without a pip install.
#
# Works from either the source tree or a deployed copy of the repo: it puts the
# repo root on PYTHONPATH so `python -m thermal_monitor` locates the package.
# The systemd unit at systemd/thermal-monitor.service runs this script.
#
# Usage:
#   ./run_monitor.sh -c thermal_monitor.yaml
#   ./run_monitor.sh -c /etc/thermal_monitor/thermal_monitor.yaml -i 60 \
#                    --json /var/lib/thermal_monitor/readings.json \
#                    --log-format systemd
#
# Alert delivery needs the `weixin_work` library importable — either
# `pip install weixin-work` or set PYTHONPATH to include its source tree.
ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m thermal_monitor "$@"
