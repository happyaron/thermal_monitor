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

log = logging.getLogger(__name__)


def _load_state(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def _save_state(path: str, state: dict) -> None:
    try:
        Path(path).write_text(json.dumps(state))
    except Exception as exc:
        log.warning("Could not save alert state: %s", exc)


def _make_sender(alerting_cfg: dict):
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
        raise RuntimeError("weixin_work library not found — pip install -e .")

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
            bot.send_markdown(content)
            if has_crit and mention_all:
                bot.send_text(
                    "🔥 Critical thermal alert in equipment room! Please check immediately.",
                    mentioned_list=["@all"],
                )

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
            app.send_markdown(content, to_user=to_user,
                              to_party=to_party, to_tag=to_tag)
            if has_crit and mention_all:
                # Escalate: broadcast a plain-text nudge to everyone.
                app.send_text(
                    "🔥 Critical thermal alert in equipment room! Please check immediately.",
                    to_user="@all",
                )

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
    Send a WeCom alert for any sensor in WARN or CRIT state, subject to
    per-sensor cooldown.  Updates ``state`` in-place.
    """
    cooldown = float(alerting_cfg.get("alert_cooldown", 300))
    mention_all_on_crit = bool(alerting_cfg.get("mention_all_on_crit", True))

    triggered = [r for r in readings if r.status in ("WARN", "CRIT")]
    if not triggered:
        log.debug("alert: no sensors in WARN/CRIT — nothing to send")
        return

    # Apply cooldown — only alert sensors not recently notified.
    due = []
    for r in triggered:
        last = state.get(r.alert_key, 0)
        remaining = cooldown - (now - last)
        if remaining > 0:
            log.debug("alert: %s suppressed by cooldown (%.0fs remaining)", r.alert_key, remaining)
        else:
            log.debug("alert: %s is due  [%s  %.1f°C]", r.alert_key, r.status, r.value)
            due.append(r)
    if not due:
        return

    has_crit = any(r.status == "CRIT" for r in due)
    ts = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
    icon = "🔥" if has_crit else "⚠️"

    # Build WeCom Markdown message (tables unsupported, use list format).
    lines = [
        f"## {icon} Thermal Alert — {ts}",
        "",
        f"> <font color=\"{'warning' if has_crit else 'comment'}\">Equipment room temperature warning</font>",
        "",
    ]

    crit_readings = [r for r in due if r.status == "CRIT"]
    warn_readings = [r for r in due if r.status == "WARN"]

    if crit_readings:
        lines.append("**🔥 CRITICAL:**")
        for r in crit_readings:
            lines.append(
                f"- <font color=\"warning\">{r.source} / {r.sensor}: "
                f"**{r.value:.1f}°C**</font>  (crit: {r.crit:.0f}°C)"
            )
        lines.append("")
    if warn_readings:
        lines.append("**⚠️ WARNING:**")
        for r in warn_readings:
            lines.append(
                f"- {r.source} / {r.sensor}: **{r.value:.1f}°C**  (warn: {r.warn:.0f}°C)"
            )
        lines.append("")

    content = "\n".join(lines)

    if dry_run:
        mode_label = alerting_cfg.get("mode", "webhook")
        width = 62
        print(f"\n{'─' * width}")
        print(_dim(f"  WeCom message preview  [{mode_label}]  (not sent)"))
        print(f"{'─' * width}")
        print(_render_wecom_md(content))
        if has_crit and mention_all_on_crit:
            print()
            print(f"  {_yellow('@all')} {_bold('Critical thermal alert in equipment room! Please check immediately.')}")
        print(f"{'─' * width}\n")
    else:
        try:
            mode_label, send_fn = _make_sender(alerting_cfg)
        except (ValueError, RuntimeError) as exc:
            log.error("Cannot send alert: %s", exc)
            return

        try:
            send_fn(content, has_crit, mention_all_on_crit)
            log.info("WeCom alert sent via %s for %d sensor(s)", mode_label, len(due))
        except Exception as exc:
            log.error("Failed to send WeCom alert via %s: %s", mode_label, exc)

    # Update cooldown state regardless of dry_run so we don't spam the terminal.
    for r in due:
        state[r.alert_key] = now
