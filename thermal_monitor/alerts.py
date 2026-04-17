from __future__ import annotations
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List
from thermal_monitor.models import ThermalReading
from thermal_monitor._ansi import _render_wecom_md, _yellow, _bold, _dim
from thermal_monitor.io_utils import atomic_write_text

log = logging.getLogger(__name__)


def _fmt(template: str, **kwargs) -> str:
    import re
    return re.sub(r"\{(\w+)\}", lambda m: str(kwargs.get(m.group(1), "")), template)


def _load_strings() -> dict:
    import re
    import json as _json
    js_file = Path(__file__).parent.parent / "translations.js"
    try:
        text = js_file.read_text(encoding="utf-8")
        raw = _json.loads(re.search(r"window\.TRANSLATIONS\s*=\s*(\{[\s\S]*\})\s*;", text).group(1))
    except Exception as exc:
        log.warning("Could not load translations.js: %s — alert text will be empty", exc)
        raw = {}

    result: dict = {}
    for lang, groups in raw.items():
        a = groups.get("alerts", {})
        result[lang] = {
            "header":            lambda ts, _a=a: _fmt(_a.get("header",           ""), ts=ts),
            "crit_header":       lambda ts, _a=a: _fmt(_a.get("critHeader",       ""), ts=ts),
            "subtitle":          a.get("subtitle",          ""),
            "crit_subtitle":     a.get("critSubtitle",      a.get("subtitle", "")),
            "crit_label":        a.get("critLabel",         ""),
            "warn_label":        a.get("warnLabel",         ""),
            "crit_suffix":       lambda crit, _a=a: _fmt(_a.get("critSuffix",     ""), crit=f"{crit:.0f}"),
            "warn_suffix":       lambda warn, _a=a: _fmt(_a.get("warnSuffix",     ""), warn=f"{warn:.0f}"),
            "escalation":        a.get("escalation",        ""),
            "overview_sources":    lambda n,  _a=a: _fmt(_a.get("overviewSources",   ""), n=n),
            "overview_sensors":    lambda n,  _a=a: _fmt(_a.get("overviewSensors",   ""), n=n),
            "partial_header":      lambda ts, _a=a: _fmt(_a.get("partialHeader",    ""), ts=ts),
            "partial_subtitle":   a.get("partialSubtitle",   ""),
            "resolved_label":     a.get("resolvedLabel",     ""),
            "resolved_suffix":    lambda prev, _a=a: _fmt(_a.get("resolvedSuffix", ""), prev=prev),
            "all_clear_header":   lambda ts, _a=a: _fmt(_a.get("allClearHeader",  ""), ts=ts),
            "all_clear_subtitle": a.get("allClearSubtitle", ""),
            "deesc_note":         a.get("deescNote",        ""),
            "more_sensors":       lambda n, _a=a: _fmt(_a.get("moreSensors", "\u2026and {n} more"), n=n),
        }
    return result


_STRINGS = _load_strings()


def _load_state(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def _save_state(path: str, state: dict) -> None:
    try:
        # Atomic replace — a SIGTERM mid-write can't leave a zero-length
        # file, which _load_state would silently treat as "no cooldown".
        atomic_write_text(path, json.dumps(state))
    except Exception as exc:
        log.warning("Could not save alert state: %s", exc)


def _make_sender(alerting_cfg: dict, escalation_text: str):
    """
    Build and return a sender callable based on alerting_cfg["mode"].

    Returns: (mode_label, send_fn)
        send_fn(content, has_crit, mention_all_on_crit) → None

    Supported modes
    ---------------
    webhook (default)
        Uses WebhookClient.  Credentials: alerting.webhook_key or
        WEIXIN_WORK_WEBHOOK_KEY env var.

    app
        Uses AppClient.  Credentials: alerting.{corp_id, corp_secret, agent_id}
        or WEIXIN_WORK_{CORP_ID,CORP_SECRET,AGENT_ID} env vars.
        Targeting: alerting.to_user / to_party / to_tag  (default: to_user "@all").
        On CRIT with mention_all_on_crit, a second text message is sent to @all
        regardless of the normal targeting.
    """
    try:
        from weixin_work import WebhookClient, AppClient  # type: ignore
    except ImportError:
        raise RuntimeError(
            "weixin_work library not found — install it with "
            "'pip install weixin-work' (or, for local development, "
            "'pip install -e /path/to/weixin_work')"
        )

    mode = alerting_cfg.get("mode", "webhook").strip().lower()

    if mode == "webhook":
        key = (alerting_cfg.get("webhook_key")
               or os.environ.get("WEIXIN_WORK_WEBHOOK_KEY", ""))
        if not key:
            raise ValueError(
                "Webhook key required: set alerting.webhook_key or "
                "WEIXIN_WORK_WEBHOOK_KEY env var."
            )
        bot = WebhookClient(key)
        label = "webhook"

        def _send(content: str, has_crit: bool, mention_all: bool) -> None:
            if has_crit and mention_all:
                bot.send_text(
                    escalation_text,
                    mentioned_list=["@all"],
                )
            bot.send_markdown(content)

    elif mode == "app":
        corp_id     = (alerting_cfg.get("corp_id")
                       or os.environ.get("WEIXIN_WORK_CORP_ID", ""))
        corp_secret = (alerting_cfg.get("corp_secret")
                       or os.environ.get("WEIXIN_WORK_CORP_SECRET", ""))
        agent_id    = (alerting_cfg.get("agent_id")
                       or os.environ.get("WEIXIN_WORK_AGENT_ID"))

        app = AppClient(corp_id=corp_id, corp_secret=corp_secret, agent_id=agent_id)

        to_user  = alerting_cfg.get("to_user",  "@all") or None
        to_party = alerting_cfg.get("to_party") or None
        to_tag   = alerting_cfg.get("to_tag")   or None
        label    = f"app → {to_user or to_party or to_tag}"

        def _send(content: str, has_crit: bool, mention_all: bool) -> None:
            if has_crit and mention_all:
                # Escalate: broadcast a plain-text nudge to everyone.
                app.send_text(
                    escalation_text,
                    to_user="@all",
                )
            app.send_markdown(content, to_user=to_user,
                              to_party=to_party, to_tag=to_tag)

    else:
        raise ValueError(
            f"Unknown alerting mode: {mode!r}  (expected 'webhook' or 'app')"
        )

    return label, _send


def _build_overview(readings: List[ThermalReading], S: dict) -> str:
    from collections import Counter
    n_sources = len(set(r.source for r in readings))
    n_sensors = len(readings)
    counts = Counter(r.status for r in readings)
    parts = [S["overview_sources"](n_sources), S["overview_sensors"](n_sensors)]
    if counts.get("CRIT"):  parts.append(f"🔥 {counts['CRIT']} CRIT")
    if counts.get("WARN"):  parts.append(f"⚠️ {counts['WARN']} WARN")
    if counts.get("ERROR"): parts.append(f"❌ {counts['ERROR']} ERR")
    if counts.get("OK"):    parts.append(f"✅ {counts['OK']} OK")
    return "> " + "  ·  ".join(parts)


def _apply_sensor_cap(crit_readings, warn_readings, cap):
    """Trim lists to fit within cap total sensors; CRIT fills first.

    Returns (crit_shown, warn_shown, n_hidden).  cap=0 means unlimited.
    """
    if cap <= 0:
        return crit_readings, warn_readings, 0
    crit_shown = crit_readings[:cap]
    remaining  = cap - len(crit_shown)
    warn_shown = warn_readings[:remaining]
    n_hidden   = (len(crit_readings) - len(crit_shown)) + (len(warn_readings) - len(warn_shown))
    return crit_shown, warn_shown, n_hidden


def send_alerts(
    readings: List[ThermalReading],
    alerting_cfg: dict,
    state: dict,
    now: float,
    dry_run: bool = False,
) -> None:
    """
    Send WeCom alerts for sensors in WARN/CRIT and recovery notifications for
    sensors that have been continuously OK for at least the cooldown period.
    Updates ``state`` in-place.
    """
    cooldown             = float(alerting_cfg.get("alert_cooldown",        900))
    alert_pending        = float(alerting_cfg.get("alert_pending",          0))
    max_sensors          = int(alerting_cfg.get("max_sensors_per_message",  0))
    mention_all_on_crit = bool(alerting_cfg.get("mention_all_on_crit", True))
    lang = alerting_cfg.get("language", "en")
    S = _STRINGS.get(lang, _STRINGS["en"])
    ts = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")

    if alert_pending > cooldown:
        log.warning(
            "alert_pending (%.0fs) > alert_cooldown (%.0fs): "
            "the first notification will arrive later than subsequent repeats",
            alert_pending, cooldown,
        )

    # ── Recovery detection ─────────────────────────────────────────────────
    # Sensors that were WARN/CRIT and have been continuously OK for ≥cooldown.
    current_by_key = {r.alert_key: r for r in readings}
    pending_recovery = []  # [(reading, prev_status)]

    for key, entry in list(state.items()):
        if not isinstance(entry, dict):
            continue
        # Entries without "ts" are still in the alert-pending window — they have
        # never actually fired an alert, so there is nothing to recover from.
        if "ts" not in entry:
            r = current_by_key.get(key)
            if r is None or r.status == "OK":
                log.debug("recovery: %s recovered during pending window (never alerted), discarding", key)
                del state[key]
            continue
        r = current_by_key.get(key)
        if r is None or r.status != "OK":
            entry.pop("first_ok_ts", None)  # still alerting or missing — reset
            continue
        if "first_ok_ts" not in entry:
            entry["first_ok_ts"] = now
        elapsed = now - entry["first_ok_ts"]
        log.debug("recovery: %s ok for %.0fs / %.0fs cooldown", key, elapsed, cooldown)
        if elapsed >= cooldown:
            pending_recovery.append((r, entry.get("status", "?")))

    # ── Alert detection ────────────────────────────────────────────────────
    _ord = {"WARN": 1, "CRIT": 2}
    triggered = [r for r in readings if r.status in ("WARN", "CRIT")]
    due = []
    deescalated = set()  # alert_keys that transitioned CRIT → WARN this cycle

    for r in triggered:
        entry = state.get(r.alert_key)

        # Normalise state entry — support legacy plain-timestamp format.
        if isinstance(entry, (int, float)):
            last_ts, last_status, pending_since = float(entry), None, None
        elif isinstance(entry, dict):
            last_ts      = float(entry.get("ts", 0))
            last_status  = entry.get("status")
            pending_since = entry.get("pending_since")
        else:
            last_ts, last_status, pending_since = 0.0, None, None

        is_escalated   = _ord.get(r.status, 0) > _ord.get(last_status, 0)
        is_deescalated = _ord.get(r.status, 0) < _ord.get(last_status, 0)

        # ── In the alert-pending window ──────────────────────────────────────
        # pending_since is set and ts is 0 (no alert has fired yet).
        if pending_since is not None and last_ts == 0:
            if is_escalated:
                log.debug("alert: %s escalated during pending [%s → %s], firing immediately",
                          r.alert_key, last_status, r.status)
                due.append(r)
            elif now - pending_since >= alert_pending:
                log.debug("alert: %s pending period elapsed (%.0fs), firing",
                          r.alert_key, now - pending_since)
                due.append(r)
            else:
                log.debug("alert: %s in pending (%.0fs / %.0fs elapsed)",
                          r.alert_key, now - pending_since, alert_pending)
            continue

        # ── Brand-new sensor ─────────────────────────────────────────────────
        is_new = last_status is None
        if is_new:
            if alert_pending > 0:
                log.debug("alert: %s new, starting pending timer (%.0fs)", r.alert_key, alert_pending)
                state[r.alert_key] = {"pending_since": now, "status": r.status}
            else:
                log.debug("alert: %s new, firing immediately", r.alert_key)
                due.append(r)
            continue

        # ── Existing sensor: escalation / de-escalation / cooldown ──────────
        # De-escalation (CRIT→WARN) is intentionally NOT an immediate trigger:
        # firing on every downgrade lets a flapping sensor spam every cycle.
        # Instead, de-escalation is noted and annotated when cooldown fires.
        remaining = cooldown - (now - last_ts)
        if is_escalated:
            log.debug("alert: %s escalated [%s → %s], firing immediately", r.alert_key, last_status, r.status)
            due.append(r)
        elif remaining <= 0:
            log.debug("alert: %s is due  [%s  %.1f°C]", r.alert_key, r.status, r.value)
            due.append(r)
            if is_deescalated:
                deescalated.add(r.alert_key)
        else:
            log.debug("alert: %s suppressed by cooldown (%.0fs remaining)", r.alert_key, remaining)

    if not due and not pending_recovery:
        log.debug("alert: nothing to send")
        return

    # ── Build message content ──────────────────────────────────────────────
    overview = _build_overview(readings, S)

    recovery_content = None
    if pending_recovery:
        all_clear = len(triggered) == 0
        if all_clear:
            lines = [
                S["all_clear_header"](ts),
                "",
                f"> <font color=\"info\">{S['all_clear_subtitle']}</font>",
                overview,
            ]
        else:
            _prev_ord = {"CRIT": 2, "WARN": 1}
            rec_sorted = sorted(
                pending_recovery,
                key=lambda x: (_prev_ord.get(x[1], 0), x[0].value),
                reverse=True,
            )
            if max_sensors > 0:
                rec_shown   = rec_sorted[:max_sensors]
                rec_hidden  = len(rec_sorted) - len(rec_shown)
            else:
                rec_shown, rec_hidden = rec_sorted, 0
            lines = [
                S["partial_header"](ts),
                "",
                f"> <font color=\"comment\">{S['partial_subtitle']}</font>",
                overview,
                "",
                S["resolved_label"],
            ]
            for r, prev in rec_shown:
                lines.append(
                    f"- {r.source} / {r.sensor}: **{r.value:.1f}°C**"
                    f"  {S['resolved_suffix'](prev)}"
                )
            if rec_hidden:
                lines.append(S["more_sensors"](rec_hidden))
        recovery_content = "\n".join(lines)

    alert_content = None
    has_crit = False
    if due:
        has_crit = any(r.status == "CRIT" for r in due)
        lines = [
            S["crit_header"](ts) if has_crit else S["header"](ts),
            "",
            f"> <font color=\"{'warning' if has_crit else 'comment'}\">"
            f"{S['crit_subtitle'] if has_crit else S['subtitle']}</font>",
            overview,
            "",
        ]
        crit_readings = sorted(
            [r for r in due if r.status == "CRIT"], key=lambda r: r.value, reverse=True
        )
        warn_readings = sorted(
            [r for r in due if r.status == "WARN"], key=lambda r: r.value, reverse=True
        )
        crit_shown, warn_shown, n_hidden = _apply_sensor_cap(crit_readings, warn_readings, max_sensors)
        if crit_shown:
            lines.append(S["crit_label"])
            for r in crit_shown:
                lines.append(
                    f"- <font color=\"warning\">{r.source} / {r.sensor}: "
                    f"**{r.value:.1f}°C**</font>  {S['crit_suffix'](r.crit)}"
                )
            lines.append("")
        if warn_shown:
            lines.append(S["warn_label"])
            for r in warn_shown:
                note = f"  {S['deesc_note']}" if r.alert_key in deescalated else ""
                lines.append(
                    f"- {r.source} / {r.sensor}: **{r.value:.1f}°C**  {S['warn_suffix'](r.warn)}{note}"
                )
            lines.append("")
        if n_hidden:
            lines.append(S["more_sensors"](n_hidden))
            lines.append("")
        alert_content = "\n".join(lines)

    # ── Send ───────────────────────────────────────────────────────────────
    # Recovery is sent first so the alert card (if any) lands last and shows
    # in the chat preview.  State is only updated on successful send.
    recovery_sent = False
    alert_sent    = False

    if dry_run:
        mode_label = alerting_cfg.get("mode", "webhook")
        width = 62
        if recovery_content:
            print(f"\n{'─' * width}")
            print(_dim(f"  WeCom recovery preview  [{mode_label}]  (not sent)"))
            print(f"{'─' * width}")
            print(_render_wecom_md(recovery_content))
            print(f"{'─' * width}\n")
        if alert_content:
            print(f"\n{'─' * width}")
            print(_dim(f"  WeCom message preview  [{mode_label}]  (not sent)"))
            print(f"{'─' * width}")
            print(_render_wecom_md(alert_content))
            if has_crit and mention_all_on_crit:
                print()
                print(f"  {_yellow('@all')} {_bold(S['escalation'])}")
            print(f"{'─' * width}\n")
        recovery_sent = True
        alert_sent    = True
    else:
        try:
            mode_label, send_fn = _make_sender(alerting_cfg, S["escalation"])
        except (ValueError, RuntimeError) as exc:
            log.error("Cannot send alert: %s", exc)
            return

        if recovery_content:
            try:
                send_fn(recovery_content, False, False)
                log.info("WeCom recovery sent via %s for %d sensor(s)",
                         mode_label, len(pending_recovery))
                recovery_sent = True
            except Exception as exc:
                log.error("Failed to send recovery notification via %s: %s", mode_label, exc)

        if alert_content:
            try:
                send_fn(alert_content, has_crit, mention_all_on_crit)
                log.info("WeCom alert sent via %s for %d sensor(s)", mode_label, len(due))
                alert_sent = True
            except Exception as exc:
                log.error("Failed to send WeCom alert via %s: %s", mode_label, exc)

    if recovery_sent:
        for r, _ in pending_recovery:
            del state[r.alert_key]

    # On failure, leave state untouched so the next cycle retries.
    if alert_sent:
        for r in due:
            state[r.alert_key] = {"ts": now, "status": r.status}
