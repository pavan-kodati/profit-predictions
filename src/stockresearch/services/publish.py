"""Morning publish: rate the shortlisted stocks, choose up to N picks per size group, review holdings, email."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import capgroup
from stockresearch.domain import practice as practice_rules
from stockresearch.services import holdings, notify, rejections, repo, reports
from stockresearch.services.runs import JobContext

log = logging.getLogger(__name__)
MAX_SCREEN_AGE_DAYS = 4


def publish(ctx: JobContext) -> str:
    c = ctx.c
    today = repo.today_ist()
    with session_scope(c.sessions) as s:
        screen_date = s.scalar(select(m.ScreenCandidate.screen_date).order_by(m.ScreenCandidate.screen_date.desc()).limit(1))
        if screen_date is None or screen_date < today - timedelta(days=MAX_SCREEN_AGE_DAYS):
            symbols: list[str] = []
        else:
            symbols = list(s.scalars(select(m.ScreenCandidate.symbol).where(m.ScreenCandidate.screen_date == screen_date)
                                     .order_by(m.ScreenCandidate.pre_score.desc())))
        pending = s.scalar(select(m.ResearchQueueItem.id).where(m.ResearchQueueItem.status == "pending").limit(1))

    report_ids: list[int] = []
    with ctx.stage("Rate shortlisted stocks") as detail:
        for sym in symbols:
            rid = reports.build_and_save(c, sym, purpose="daily", run_id=ctx.run_id)
            if rid:
                report_ids.append(rid)
        detail.append(f"{len(report_ids)} reports from the {screen_date} screen")
        if pending:
            detail.append("Some research was still queued; those stocks were rated on stored data")

    with ctx.stage("Choose picks") as detail:
        picks = choose_picks(ctx, report_ids)
        detail.append(", ".join(f"{p.symbol} ({p.rating:.1f})" for p in picks) or "none cleared the bar")

    ctx.soft_stage("Review holdings", lambda d: holdings.review_all(c, ctx.run_id, d))
    with ctx.stage("Send morning email") as detail:
        sent = notify.daily_email(c, ctx.run_id, screen_date, pending=bool(pending))
        detail.append(", ".join(sent) or "no notifier configured")
    ctx.soft_stage("Send pending alerts", lambda d: d.append(f"{notify.send_pending_alerts(c)} alert(s) sent"))
    return f"{len(picks)} pick(s) from {len(report_ids)} rated stocks"


STILL_WAITING_DAYS = 7  # a pick with no pretend trade after this long is treated as finished
CROWDED = "A good case, but stronger picks filled its size group today."


def _repeat_block(s: Session, symbol: str, today: date) -> str | None:
    """Why this stock can't be a new pick today (plan v3: a stock is a new pick once), or None."""
    last = s.scalar(select(m.Pick).where(m.Pick.symbol == symbol, m.Pick.pick_date < today)
                    .order_by(m.Pick.pick_date.desc()).limit(1))
    if last is None:
        return None
    trade = s.scalar(select(m.PracticeTrade).where(m.PracticeTrade.pick_id == last.id))
    if trade is not None:
        status: str | None = trade.status
    else:
        status = None if (today - last.pick_date).days <= STILL_WAITING_DAYS else "skipped"
    newest = s.scalar(select(func.max(m.Event.event_date)).where(
        m.Event.symbol == symbol, m.Event.polarity > 0, m.Event.status == "verified"))
    return practice_rules.repeat_block(last.pick_date, status, newest)


def choose_picks(ctx: JobContext, report_ids: list[int]) -> list[m.Pick]:
    """Up to `picks_per_group` new picks in each size group, best rating first. Stocks already picked and still
    running are held back, and everything else that was rated is recorded as a rejection to follow."""
    c = ctx.c
    today = repo.today_ist()
    with session_scope(c.sessions) as s:
        rows = [r for r in (s.get(m.Report, i) for i in report_ids) if r is not None]
        for old in s.scalars(select(m.Pick).where(m.Pick.pick_date == today)):
            s.delete(old)
        s.flush()
        eligible = sorted(
            (r for r in rows if r.excluded_reason is None and r.rating >= c.settings.pick_min_rating),
            key=lambda r: (r.rating, r.completeness), reverse=True,
        )
        taken = dict.fromkeys(capgroup.ORDER, 0)
        picks = []
        for r in eligible:
            group = capgroup.resolve(r.data.get("cap_group"))
            held_back = _repeat_block(s, r.symbol, today) or (CROWDED if taken[group] >= c.settings.picks_per_group else None)
            if held_back:
                r.data = {**r.data, "held_back": held_back}
                continue
            taken[group] += 1
            r.is_pick = True
            tech = r.data["tech"]
            p = m.Pick(report_id=r.id, symbol=r.symbol, pick_date=today, ref_close=tech["close"],
                       ref_date=r.data_as_of or today, rating=r.rating, pick_type=r.pick_type)
            s.add(p)
            picks.append(p)
        s.flush()
        rejections.record(s, rows, today, c.settings.pick_min_rating)
        return picks
