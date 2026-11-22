"""Track record (plan G4, E2, E3): what every pick actually did, from the next day's opening price, after costs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain.costs import CostModel, net_return
from stockresearch.domain.practice import earliest_entry_day
from stockresearch.services import notify, repo

STALE_GAP = 0.03
TRACK_DAYS = 45


def cost_model(c: Container) -> CostModel:
    st = c.settings
    return CostModel(st.brokerage_per_order, st.dp_charge_per_sell, st.short_term_tax_rate, st.long_term_tax_rate)


def update_outcomes(c: Container, detail: list[str]) -> None:
    since = repo.today_ist() - timedelta(days=TRACK_DAYS)
    model = cost_model(c)
    with session_scope(c.sessions) as s:
        picks = list(s.scalars(select(m.Pick).where(m.Pick.pick_date >= since)))
        bars = repo.load_bars(s, sorted({p.symbol for p in picks}), days=TRACK_DAYS + 30)
        updated = 0
        for p in picks:
            df = bars.get(p.symbol)
            if df is None:
                continue
            report = s.get(m.Report, p.report_id)
            first_day = earliest_entry_day(p.pick_date, report.created_at if report else repo.now_ist())
            after = df[(df.index.date > p.ref_date) & (df.index.date >= first_day)]  # type: ignore[attr-defined]
            if after.empty:
                continue
            entry = float(after["open"].iloc[0]) or float(after["close"].iloc[0])
            closes = after["close"]

            def ret(n: int, closes: Any = closes, entry: float = entry) -> float | None:
                return float(closes.iloc[n] / entry - 1) if len(closes) > n else None

            o = p.outcome or m.PickOutcome(pick_id=p.id)
            o.entry_price = round(entry, 2)
            o.entry_date = after.index[0].date()
            o.stale = o.stale or entry > p.ref_close * (1 + STALE_GAP)
            o.ret_5, o.ret_10, o.ret_15 = ret(5), ret(10), ret(15)
            o.net_ret_10 = net_return(o.ret_10, c.settings.reference_investment, model) if o.ret_10 is not None else None
            o.max_gain = float(after["high"].max() / entry - 1)
            o.max_drawdown = float(after["low"].min() / entry - 1)
            o.days_tracked = len(closes)
            o.updated_at = repo.now_ist()
            if p.outcome is None:
                s.add(o)
            updated += 1
    detail.append(f"{updated} pick(s) updated")


def open_check(c: Container, detail: list[str]) -> None:
    """After the open: flag today's picks that opened more than 3% above the reference price as stale."""
    today = repo.today_ist()
    with session_scope(c.sessions) as s:
        picks = list(s.scalars(select(m.Pick).where(m.Pick.pick_date == today)))
    if not picks:
        detail.append("No picks today")
        return
    opens = c.market.today_open([p.symbol for p in picks])
    stale = []
    with session_scope(c.sessions) as s:
        for p in picks:
            price = opens.get(p.symbol)
            if price is None:
                continue
            gap = price / p.ref_close - 1
            if gap > STALE_GAP:
                row = s.get(m.Pick, p.id)
                if row is not None:
                    o = row.outcome or m.PickOutcome(pick_id=row.id, updated_at=repo.now_ist())
                    o.stale = True
                    if row.outcome is None:
                        s.add(o)
                stale.append(f"{p.symbol} opened {gap * 100:+.1f}% above the reference price (Rs {p.ref_close:,.2f} -> "
                             f"Rs {price:,.2f}); much of the expected move may already have happened")
                repo.add_alert(s, f"stale:{p.id}", p.symbol, "stale_pick", "warn", stale[-1])
    detail.append(f"{len(opens)} opening prices; {len(stale)} stale")
    if stale:
        notify.send_pending_alerts(c)


def summary(picks: list[m.Pick]) -> dict[str, Any]:
    """Scorecard by star rating and by label, gross and after costs."""
    groups: dict[str, list[m.Pick]] = {}
    for p in picks:
        if p.outcome is None or p.outcome.ret_10 is None:
            continue
        groups.setdefault(f"{int(p.rating + 0.5)} stars", []).append(p)
        groups.setdefault(p.pick_type, []).append(p)
        groups.setdefault("All picks", []).append(p)
    out = {}
    for name, ps in groups.items():
        gross = [p.outcome.ret_10 for p in ps if p.outcome and p.outcome.ret_10 is not None]
        net = [p.outcome.net_ret_10 for p in ps if p.outcome and p.outcome.net_ret_10 is not None]
        out[name] = {
            "count": len(gross),
            "hit_rate": sum(1 for g in gross if g > 0) / len(gross) if gross else None,
            "avg_gross": sum(gross) / len(gross) if gross else None,
            "avg_net": sum(net) / len(net) if net else None,
        }
    return out
