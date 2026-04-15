#!/bin/bash
# setup_venv.sh — create or refresh the local venv used by run_monitor.sh.
#
# Creates <ROOT>/venv and pip-installs thermal_monitor (this directory),
# plus weixin_work if a sibling ../weixin_work source tree is present
# (required for WeCom alert delivery).  Idempotent — safe to re-run after
# pulling new code; -e installs mean source edits take effect immediately.
#
# Once this has been run, run_monitor.sh automatically prefers
# <ROOT>/venv/bin/python3 over the system Python.
#
# Usage:
#   ./setup_venv.sh                  # use `python3` from PATH
#   PYTHON=python3.11 ./setup_venv.sh  # pin a specific interpreter

set -euo pipefail

ROOT="$(dirname "$(readlink -f "$0")")"
VENV="$ROOT/venv"
PY="${PYTHON:-python3}"

if [ ! -x "$VENV/bin/python3" ]; then
    echo "Creating venv at $VENV using $PY..."
    "$PY" -m venv "$VENV"
fi

# Recent pip is required: older versions can't build PEP 517 projects that
# only declare setuptools>=61 in build-system.requires.
"$VENV/bin/pip" install --upgrade pip

# Install thermal_monitor itself (editable, from this directory).
"$VENV/bin/pip" install -e "$ROOT"

# Install weixin_work if the sibling source tree is present; skip otherwise
# so the venv still works for deployments that don't need WeCom alerting.
WEIXIN="$ROOT/../weixin_work"
if [ -f "$WEIXIN/pyproject.toml" ]; then
    "$VENV/bin/pip" install -e "$WEIXIN"
else
    echo
    echo "Note: $WEIXIN not found — skipping weixin_work install."
    echo "      WeCom alert delivery will be disabled.  To enable it, place"
    echo "      the weixin_work source tree next to thermal_monitor/ and"
    echo "      re-run this script."
fi

echo
echo "Done.  Run the monitor with:"
echo "    $ROOT/run_monitor.sh -c path/to/thermal_monitor.yaml"
