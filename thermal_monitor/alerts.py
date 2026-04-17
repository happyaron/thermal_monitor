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
            "resolved_header":   lambda ts, _a=a: _fmt(_a.get("resolvedHeader",   ""), ts=ts),
            "resolved_subtitle": a.get("resolvedSubtitle",  ""),
            "resolved_label":    a.get("resolvedLabel",     ""),
            "resolved_suffix":   lambda prev, _a=a: _fmt(_a.get("resolvedSuffix", ""), prev=prev),
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


def send_alerts(
    readings: List[ThermalReading],
    alerting_cfg: dict,
    state: dict,
    now: float,
    dry_run: bool = False,
) -> None:
    """
    Send WeCom alerts for sensors in WARN/CRIT and recovery notifications for
    sensors that have returned to OK for at least 2 consecutive cycles.
    Updates ``state`` in-place.
    """
    cooldown = float(alerting_cfg.get("alert_cooldown", 900))
    mention_all_on_crit = bool(alerting_cfg.get("mention_all_on_crit", True))
    lang = alerting_cfg.get("language", "en")
    S = _STRINGS.get(lang, _STRINGS["en"])
    ts = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")

    # ── Recovery detection ─────────────────────────────────────────────────
    # Sensors that were WARN/CRIT and are now OK for ≥2 consecutive cycles.
    current_by_key = {r.alert_key: r for r in readings}
    pending_recovery = []  # [(reading, prev_status)]

    for key, entry in list(state.items()):
        if not isinstance(entry, dict):
            continue
        r = current_by_key.get(key)
        if r is None or r.status != "OK":
            entry.pop("ok_streak", None)  # still alerting or missing — reset streak
            continue
        entry["ok_streak"] = entry.get("ok_streak", 0) + 1
        log.debug("recovery: %s ok_streak=%d", key, entry["ok_streak"])
        if entry["ok_streak"] >= 2:
            pending_recovery.append((r, entry.get("status", "?")))

    # ── Alert detection ────────────────────────────────────────────────────
    _ord = {"WARN": 1, "CRIT": 2}
    triggered = [r for r in readings if r.status in ("WARN", "CRIT")]
    due = []

    for r in triggered:
        entry = state.get(r.alert_key)

        # Normalise state entry — support legacy plain-timestamp format.
        if isinstance(entry, (int, float)):
            last_ts, last_status = float(entry), None
        elif isinstance(entry, dict):
            last_ts     = float(entry.get("ts", 0))
            last_status = entry.get("status")
        else:
            last_ts, last_status = 0.0, None

        is_new         = last_status is None
        is_escalated   = _ord.get(r.status, 0) > _ord.get(last_status, 0)
        is_deescalated = _ord.get(r.status, 0) < _ord.get(last_status, 0)
        remaining      = cooldown - (now - last_ts)

        if is_new or is_escalated or is_deescalated:
            log.debug("alert: %s immediate [%s → %s]", r.alert_key, last_status, r.status)
            due.append(r)
        elif remaining <= 0:
            log.debug("alert: %s is due  [%s  %.1f°C]", r.alert_key, r.status, r.value)
            due.append(r)
        else:
            log.debug("alert: %s suppressed by cooldown (%.0fs remaining)", r.alert_key, remaining)

    if not due and not pending_recovery:
        log.debug("alert: nothing to send")
        return

    # ── Build message content ──────────────────────────────────────────────
    recovery_content = None
    if pending_recovery:
        lines = [
            S["resolved_header"](ts),
            "",
            f"> <font color=\"info\">{S['resolved_subtitle']}</font>",
            "",
            S["resolved_label"],
        ]
        for r, prev in pending_recovery:
            lines.append(
                f"- {r.source} / {r.sensor}: **{r.value:.1f}°C**"
                f"  {S['resolved_suffix'](prev)}"
            )
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
            "",
        ]
        crit_readings = [r for r in due if r.status == "CRIT"]
        warn_readings = [r for r in due if r.status == "WARN"]
        if crit_readings:
            lines.append(S["crit_label"])
            for r in crit_readings:
                lines.append(
                    f"- <font color=\"warning\">{r.source} / {r.sensor}: "
                    f"**{r.value:.1f}°C**</font>  {S['crit_suffix'](r.crit)}"
                )
            lines.append("")
        if warn_readings:
            lines.append(S["warn_label"])
            for r in warn_readings:
                lines.append(
                    f"- {r.source} / {r.sensor}: **{r.value:.1f}°C**  {S['warn_suffix'](r.warn)}"
                )
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
