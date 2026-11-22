"""Notifications: the morning email, holding alerts, and job-failure alerts (plan E9)."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import escape
from sqlalchemy import select

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import capgroup
from stockresearch.formatting import inr
from stockresearch.services import repo

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).resolve().parent.parent / "notifications" / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
_env.filters["inr"] = inr
_env.filters["inr2"] = lambda x: inr(x, 2)
DISCLAIMER = ("Automated research, not investment advice and not a prediction. Prices can fall as well as rise. "
              "Check the linked sources yourself before investing.")


def _send(c: Container, subject: str, html: str, text: str) -> list[str]:
    sent = []
    for n in c.notifiers:
        try:
            n.send(subject, html, text)
            sent.append(n.name)
        except Exception as e:
            log.error("notifier %s failed: %s", n.name, e)
    return sent


def pick_view(r: m.Report, settings_url: str, amount: float) -> dict[str, Any]:
    from stockresearch.presenters import plain_pick

    pl = plain_pick(r, amount, repo.today_ist())
    return {
        "symbol": r.symbol, "name": r.data.get("name", r.symbol), "rating": r.rating, "close": r.data["tech"]["close"],
        "group": capgroup.resolve(r.data.get("cap_group")), "plain": pl,
        "evidence": [e for e in r.evidence if e.stance == "for"][:3],
        "url": f"{settings_url}/reports/{r.id}",
    }


def grouped(views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Picks split into the three size groups, strongest rating first. Every group is listed, even if empty."""
    return [{"key": g, "label": capgroup.LABEL[g], "singular": capgroup.SINGULAR[g], "hint": capgroup.HINT[g],
             "picks": sorted((v for v in views if v["group"] == g), key=lambda v: -v["rating"])}
            for g in capgroup.ORDER]


def daily_email(c: Container, run_id: int, screen_date: date | None, pending: bool) -> list[str]:
    from stockresearch.services import practice

    today = repo.today_ist()
    amount = c.settings.practice_amount
    with session_scope(c.sessions) as s:
        picks = list(s.scalars(select(m.Report).where(m.Report.run_id == run_id, m.Report.is_pick.is_(True))
                               .order_by(m.Report.rating.desc())))
        rated = s.scalar(select(m.Report.id).where(m.Report.run_id == run_id).limit(1)) is not None
        alerts = list(s.scalars(select(m.Alert).where(m.Alert.sent.is_(False)).order_by(m.Alert.created_at)))
        views = [pick_view(r, c.settings.app_url, amount) for r in picks]
        record = practice.summarise(c, s)
        new_symbols = {v["symbol"] for v in views}
        ctx = {
            "today": today, "screen_date": screen_date, "picks": views, "groups": grouped(views), "rated": rated,
            "pending": pending, "amount": amount, "names": ", ".join(v["name"] for v in views),
            "alerts": [{"symbol": a.symbol, "severity": a.severity, "message": a.message} for a in alerts],
            "app_url": c.settings.app_url, "disclaimer": DISCLAIMER, "ai": c.researcher is not None,
            "min_rating": c.settings.pick_min_rating,
            "running": [v for v in record.trades if v.t.status == "open" and v.t.symbol not in new_symbols],
            "waiting": [w for w in practice.waiting_picks(s) if w.pick.symbol not in new_symbols],
        }
        html = _env.get_template("daily.html").render(**ctx)
        text = _env.get_template("daily.txt").render(**ctx)
        if views:
            subject = f"{len(views)} new pick{'s' if len(views) != 1 else ''} for {today:%a %d %b}: " + ", ".join(
                v["symbol"] for v in views)
        else:
            subject = f"No new picks for {today:%a %d %b}"
        sent = _send(c, subject, html, text)
        if sent:
            for a in alerts:
                a.sent = True
        return sent


def send_pending_alerts(c: Container) -> int:
    with session_scope(c.sessions) as s:
        alerts = list(s.scalars(select(m.Alert).where(m.Alert.sent.is_(False)).order_by(m.Alert.created_at)))
        if not alerts:
            return 0
        lines = [f"[{a.severity.upper()}] {a.message}" for a in alerts]
        text = "\n".join(lines) + f"\n\nHoldings: {c.settings.app_url}/holdings\n\n{DISCLAIMER}"
        html = _env.get_template("alerts.html").render(alerts=alerts, app_url=c.settings.app_url, disclaimer=DISCLAIMER)
        worst = "Exit alert" if any(a.severity == "exit" for a in alerts) else "Alert"
        if _send(c, f"{worst}: {', '.join(sorted({a.symbol for a in alerts}))}", html, text):
            for a in alerts:
                a.sent = True
            return len(alerts)
    return 0


def failure_alert(c: Container, job: str, error: str) -> None:
    """At most one failure alert per job every 6 hours, so an outage does not flood the inbox."""
    now = repo.now_ist()
    key = f"failure:{job}:{now:%Y%m%d}:{now.hour // 6}"
    with session_scope(c.sessions) as s:
        if not repo.add_alert(s, key, "-", "job_failed", "warn", f"The {job} job failed: {error}"):
            return
        alert_id = s.scalar(select(m.Alert.id).where(m.Alert.dedupe_key == key))
    text = (f"The '{job}' job failed at {now:%d %b %H:%M} IST.\n\nError: {error}\n\n"
            f"Run history: {c.settings.app_url}/runs")
    html = (f"<p>The <b>{escape(job)}</b> job failed at {now:%d %b %H:%M} IST.</p><pre>{escape(error)}</pre>"
            f"<p><a href='{c.settings.app_url}/runs'>Run history</a></p>")
    if _send(c, f"Stock research: {job} failed", html, text):
        with session_scope(c.sessions) as s:
            a = s.get(m.Alert, alert_id)
            if a:
                a.sent = True


MAX_ROWS = 12


def weekly_results(c: Container) -> list[str]:
    """Friday email: how the app's own picks did, in plain words, with the reason each one moved."""
    from stockresearch.presenters import TRADE_EXIT_WORDS
    from stockresearch.services import practice, rejections

    today = repo.today_ist()
    with session_scope(c.sessions) as s:
        data = practice.week_summary(c, s, today)
        summary = data["summary"]
        everything = [v for v in summary.trades if v.t.status in ("open", "closed")]  # newest first
        rej = rejections.summarise(s, limit=20)
        ctx = {**data, "rows": everything[:MAX_ROWS], "more": max(len(everything) - MAX_ROWS, 0), "rej": rej, "today": today,
               "app_url": c.settings.app_url, "disclaimer": DISCLAIMER, "amount": c.settings.practice_amount,
               "exit_words": TRADE_EXIT_WORDS}
        html = _env.get_template("weekly.html").render(**ctx)
        text = _env.get_template("weekly.txt").render(**ctx)
        if summary.invested:
            subject = (f"How the app's picks did, week to {today:%d %b}: "
                       f"{'up' if summary.profit >= 0 else 'down'} Rs {abs(summary.profit):,.0f} after costs")
        else:
            subject = f"How the app's picks did, week to {today:%d %b}: no trades yet"
        return _send(c, subject, html, text)
