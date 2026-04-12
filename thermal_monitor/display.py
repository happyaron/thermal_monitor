from __future__ import annotations
import sys
from datetime import datetime
from typing import Dict, List, Optional, Union
from thermal_monitor.models import ThermalReading, STATUS_ORD, STATUS_LABEL
from thermal_monitor.analysis import most_urgent, primary_inlet, alert_hint, _abbrev_name
from thermal_monitor._ansi import _red, _yellow, _green, _bold, _dim, _orange


def _status_text(status: str) -> str:
    if status == "OK":    return _green("✓ OK  ")
    if status == "WARN":  return _yellow("⚠ WARN")
    if status == "CRIT":  return _red("✖ CRIT")
    return _dim("? ERR ")


def print_table(
    readings: List[ThermalReading],
    source_groups: Optional[Dict[str, str]] = None,
    primary_sensors: Optional[Dict[str, Union[str, List[str]]]] = None,
) -> None:
    """Compact per-source summary table.

    TEMP column shows the primary inlet sensor (best ambient representative).
    STATUS column shows the worst status across all sensors.  When the alert
    source differs from the displayed sensor an inline hint names the culprit.

    Groups whose members are all OK are collapsed to a single header line.
    Non-OK groups auto-expand to show member rows with sensor detail.
    """
    source_groups = source_groups or {}
    primary_sensors = primary_sensors or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{_bold('THERMAL MONITOR')}  {_dim(now)}\n")

    # Collect and sort sources: singles first, then groups, alphabetically.
    by_source: Dict[str, List[ThermalReading]] = {}
    for r in readings:
        by_source.setdefault(r.source, []).append(r)

    def _sort_key(name: str):
        grp = source_groups.get(name)
        return (1 if grp else 0, grp or "", name)

    source_order = sorted(by_source, key=_sort_key)

    # Column width based on displayed names (computed after group summaries).
    display_names = {n: _abbrev_name(n, source_groups.get(n)) for n in source_order}

    # Pre-compute group-level summaries.
    group_summaries: Dict[str, dict] = {}
    for src_name in source_order:
        grp = source_groups.get(src_name)
        if not grp:
            continue
        src_readings = by_source[src_name]
        worst = max(STATUS_ORD[r.status] for r in src_readings)
        pri = primary_inlet(src_readings, primary_sensors.get(src_name))
        if grp not in group_summaries:
            group_summaries[grp] = {
                "worst": 0, "primary": None, "count": 0,
                "non_ok_members": 0, "alert_hint": None,
            }
        gs = group_summaries[grp]
        gs["worst"]  = max(gs["worst"], worst)
        gs["count"] += 1
        if worst > 0:
            gs["non_ok_members"] += 1
        # Group primary: highest primary inlet value across members
        # (warmest intake in the rack).
        if pri is not None:
            if gs["primary"] is None or pri.value > gs["primary"].value:
                gs["primary"] = pri

    # Build group alert hints.
    for grp, gs in group_summaries.items():
        if gs["worst"] == 0:
            continue   # all OK — no hint
        n_bad = gs["non_ok_members"]
        if n_bad > 1:
            gs["alert_hint"] = f"{n_bad} hosts"
        elif n_bad == 1:
            # Find the single non-OK member and its alert sensor.
            for sn in source_order:
                if source_groups.get(sn) != grp:
                    continue
                src_r = by_source[sn]
                w = max(STATUS_ORD[r.status] for r in src_r)
                if w == 0:
                    continue
                short = display_names[sn]
                sensor_hint = alert_hint(src_r, primary_inlet(src_r, primary_sensors.get(sn)))
                if sensor_hint:
                    gs["alert_hint"] = f"{short}: {sensor_hint}"
                else:
                    gs["alert_hint"] = short
                break

    # Compute column width: must accommodate source names AND group headers.
    src_w = max((len(v) for v in display_names.values()), default=6)
    src_w = max(src_w, 6)
    # Include group names in width computation.
    for grp in group_summaries:
        src_w = max(src_w, len(grp))

    hdr = f"  {'Source':<{src_w}}  {'Temp':>7}  Status"
    sep = "  " + "─" * (src_w + 24)
    print(_bold(hdr))
    print(_dim(sep))

    printed_groups: set = set()
    prev_was_expanded_group = False   # track whether we need a blank line

    for src_name in source_order:
        grp = source_groups.get(src_name)

        if grp:
            gs = group_summaries[grp]
            is_expanded = gs["worst"] > 0

            # ── group header (print once per group) ───────────────────────
            if grp not in printed_groups:
                # Blank line before an expanded group to separate it visually.
                if is_expanded and not prev_was_expanded_group:
                    print()
                # Close previous expanded group with a blank line.
                if prev_was_expanded_group:
                    print()
                    prev_was_expanded_group = False

                g_label  = STATUS_LABEL[gs["worst"]]
                g_pri    = gs["primary"]
                g_temp_s = f"{g_pri.value:>6.1f}°" if g_pri is not None else "   ---"
                if   g_label == "CRIT": g_temp_s = _red(g_temp_s)
                elif g_label == "WARN": g_temp_s = _yellow(g_temp_s)
                g_count  = gs["count"]

                if is_expanded:
                    # Expanded group: decorated header with ── dashes
                    hint_s = ""
                    if gs["alert_hint"]:
                        hint_s = _dim(f"  ({gs['alert_hint']})")
                    g_name = f"── {grp} ({g_count}) "
                    fill = "─" * max(0, src_w - len(g_name))
                    print(
                        _dim(f"  {g_name}{fill}") +
                        f"  {g_temp_s}  {_status_text(g_label)}" +
                        hint_s
                    )
                    prev_was_expanded_group = True
                else:
                    # Collapsed group: ▸ prefix + dim count suffix distinguish it from singles
                    suffix = _dim(f"  ({g_count} hosts)")
                    print(
                        _dim("▸ ") + f"{grp:<{src_w}}" +
                        f"  {g_temp_s}  {_status_text(g_label)}" +
                        suffix
                    )
                printed_groups.add(grp)

            # Skip member rows for OK groups.
            if not is_expanded:
                continue
        else:
            # Close previous expanded group with a blank line.
            if prev_was_expanded_group:
                print()
                prev_was_expanded_group = False

        # ── source summary row ────────────────────────────────────────────
        src_readings = by_source[src_name]
        valid  = [r for r in src_readings if not r.error]
        errors = [r for r in src_readings if r.error]
        worst  = max(STATUS_ORD[r.status] for r in src_readings)
        worst_label = STATUS_LABEL[worst]

        disp = display_names[src_name]
        indent = "     " if grp else "  "

        pri = primary_inlet(src_readings, primary_sensors.get(src_name))
        if pri is not None:
            temp_s = f"{pri.value:>6.1f}°"
            if   worst_label == "CRIT": temp_s = _red(temp_s)
            elif worst_label == "WARN": temp_s = _yellow(temp_s)
        else:
            temp_s = f"{'---':>7}"

        hint_s = ""
        hint = alert_hint(src_readings, pri)
        if hint:
            hint_s = _dim(f"  ({hint})")

        row = f"{indent}{disp:<{src_w}}  {temp_s}  {_status_text(worst_label)}{hint_s}"
        if not valid and errors:
            row += f"  {_dim(errors[0].error[:50])}"
        print(row)

        # ── sensor detail (non-OK sources only) ───────────────────────────
        if worst_label != "OK":
            detail = sorted(src_readings, key=lambda x: x.sensor)
            sen_w  = max(len(r.sensor) for r in detail)
            det_indent = indent + "   "
            for j, r in enumerate(detail):
                connector = "└─" if j == len(detail) - 1 else "├─"
                if r.error:
                    print(f"{det_indent}{connector} {r.sensor:<{sen_w}}  {'---':>7}"
                          f"          {_dim(r.error[:40])}")
                else:
                    rt = f"{r.value:>6.1f}°"
                    if   r.status == "CRIT": rt = _red(rt)
                    elif r.status == "WARN": rt = _yellow(rt)
                    thresh = _dim(f"({r.warn:.0f}/{r.crit:.0f})")
                    mark   = {"CRIT": _red("✖"), "WARN": _yellow("⚠")}.get(r.status, " ")
                    print(f"{det_indent}{connector} {r.sensor:<{sen_w}}  {rt}  {thresh}  {mark}")

    print(_dim(sep))

    n_ok   = sum(1 for r in readings if r.status == "OK")
    n_warn = sum(1 for r in readings if r.status == "WARN")
    n_crit = sum(1 for r in readings if r.status == "CRIT")
    n_err  = sum(1 for r in readings if r.status == "ERROR")

    parts = [f"{_bold(str(n_ok))} OK"]
    if n_warn: parts.append(_yellow(f"{n_warn} WARN"))
    if n_crit: parts.append(_red(f"{n_crit} CRIT"))
    if n_err:  parts.append(_dim(f"{n_err} ERR"))
    counts = f"{len(by_source)} sources, {len(readings)} sensors"
    print(f"  {'  '.join(parts)}  {_dim(f'({counts})')}\n")
