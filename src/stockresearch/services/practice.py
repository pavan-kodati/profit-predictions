"""Pretend trades (plan v2 section 2, plan v3 sections 4 and 5).

Two separate books share the same rules, costs and tax:
- the app's own track record: every morning pick becomes a trade automatically (`PracticeTrade`);
- the user's practice portfolio: trades the user adds with "Practise buying this" (`UserTrade`).

Everything is derived from stored daily prices, so the job is idempotent and can catch up from any date without
using prices that weren't known at the time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from stockresearch.adapters.yahoo import INDEX_SYMBOL
from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import attribution, capgroup
from stockresearch.domain import practice as rules
from stockresearch.domain.models import PickType
from stockresearch.services import reasons, repo
from stockresearch.services.tracker import cost_model

log = logging.getLogger(__name__)
Trade = m.PracticeTrade | m.UserTrade
TradeTable = type[m.PracticeTrade] | type[m.UserTrade]
MIN_AMOUNT, MAX_AMOUNT = 1_000.0, 10_00_000.0


class PracticeError(ValueError):
    pass


def _bars(df: pd.DataFrame, after: date | None = None, from_day: date | None = None) -> list[rules.Bar]:
    out = []
    for ts, r in zip(pd.to_datetime(df.index), df.to_dict("records"), strict=True):
        d = ts.date()
        if (after and d <= after) or (from_day and d < from_day):
            continue
        out.append(rules.Bar(d, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    return out


def _close(t: Trade, exit_: rules.Exit, model: Any) -> None:
    assert t.entry_price is not None
    result = rules.settle(t.quantity, t.entry_price, exit_.price, model)
    t.status, t.exit_date, t.exit_price, t.exit_reason = "closed", exit_.day, round(exit_.price, 2), exit_.reason
    t.charges, t.tax, t.net_pnl, t.updated_at = result.charges, result.tax, result.net, repo.now_ist()


def sync(c: Container, detail: list[str]) -> None:
    """Open trades for new picks and new user requests, close trades whose exit rule fired, refresh the reasons."""
    model = cost_model(c)
    amount = c.settings.practice_amount
    opened = closed = skipped = 0
    with session_scope(c.sessions) as s:
        done = set(s.scalars(select(m.PracticeTrade.pick_id)))
        picks = [p for p in s.scalars(select(m.Pick).order_by(m.Pick.pick_date, m.Pick.id)) if p.id not in done]
        live: list[Trade] = list(s.scalars(select(m.PracticeTrade).where(m.PracticeTrade.status == "open")))
        live += list(s.scalars(select(m.UserTrade).where(m.UserTrade.status == "open")))
        pending = list(s.scalars(select(m.UserTrade).where(m.UserTrade.status == "pending")))
        symbols = sorted({p.symbol for p in picks} | {t.symbol for t in live} | {u.symbol for u in pending})
        prices = repo.load_bars(s, [*symbols, INDEX_SYMBOL], days=120) if symbols else {}

        for p in picks:
            df = prices.get(p.symbol)
            report = s.get(m.Report, p.report_id)
            published = report.created_at if report else repo.now_ist()
            first_day = rules.earliest_entry_day(p.pick_date, published)
            later = _bars(df, after=p.ref_date, from_day=first_day) if df is not None else []
            if not later:
                continue  # the first tradeable day's prices aren't stored yet
            stop = float((report.data.get("levels") or {}).get("stop") or 0) if report else 0.0
            entry = rules.plan_entry(later[0].open, stop, amount)
            now = repo.now_ist()
            trade = m.PracticeTrade(
                pick_id=p.id, symbol=p.symbol, rules_version=rules.RULES_VERSION, pick_date=p.pick_date,
                pick_type=p.pick_type, rating=p.rating, amount=round(amount, 2), stop_price=stop,
                hold_days=rules.HOLD_DAYS.get(PickType(p.pick_type), 10), entry_date=later[0].day,
                entry_price=round(entry.price, 2), quantity=entry.quantity,
                buy_value=round(entry.quantity * entry.price, 2), created_at=now, updated_at=now,
                status="skipped" if entry.skip_reason else "open", skip_reason=entry.skip_reason,
            )
            s.add(trade)
            if entry.skip_reason:
                skipped += 1
            else:
                opened += 1
                live.append(trade)

        for u in pending:
            df = prices.get(u.symbol)
            first_day = rules.earliest_entry_day(u.requested_at.date(), u.requested_at)
            later = _bars(df, from_day=first_day) if df is not None else []
            if not later:
                continue
            entry = rules.plan_entry(later[0].open, u.stop_price, u.amount)
            u.entry_date, u.entry_price, u.quantity = later[0].day, round(entry.price, 2), entry.quantity
            u.buy_value, u.updated_at = round(entry.quantity * entry.price, 2), repo.now_ist()
            u.status, u.skip_reason = ("skipped", entry.skip_reason) if entry.skip_reason else ("open", None)
            if entry.skip_reason:
                skipped += 1
            else:
                opened += 1
                live.append(u)
        s.flush()

        for t in live:
            df = prices.get(t.symbol)
            if df is None or t.entry_date is None or t.entry_price is None:
                continue
            bars = _bars(df, from_day=t.entry_date)
            exit_ = rules.find_exit(bars, t.stop_price, t.hold_days)
            if isinstance(t, m.UserTrade) and t.sell_requested_at is not None:
                sell_day = rules.earliest_entry_day(t.sell_requested_at.date(), t.sell_requested_at)
                manual = rules.manual_exit(bars, max(sell_day, t.entry_date))
                if manual and (exit_ is None or manual.day <= exit_.day):
                    exit_ = manual
            if exit_ is not None:
                _close(t, exit_, model)
                closed += 1
        explained = reasons.update(s, live, prices, prices.get(INDEX_SYMBOL), repo.latest_price_date(s))
    detail.append(f"{opened} opened, {closed} closed, {skipped} skipped, {explained} reason(s) refreshed")


# ---------------------------------------------------------------- the user's own trades


def add_user_trade(c: Container, report_id: int, amount: float) -> m.UserTrade:
    """Queue a pretend purchase of the stock in a report. It is bought at the next market open."""
    if not MIN_AMOUNT <= amount <= MAX_AMOUNT:
        raise PracticeError(f"Choose an amount between Rs {MIN_AMOUNT:,.0f} and Rs {MAX_AMOUNT:,.0f}")
    with session_scope(c.sessions) as s:
        r = s.get(m.Report, report_id)
        if r is None:
            raise PracticeError("Report not found")
        stop = float((r.data.get("levels") or {}).get("stop") or 0)
        if stop <= 0:
            raise PracticeError("This report has no safety level, so it can't be practised")
        if s.scalar(select(m.UserTrade.id).where(m.UserTrade.symbol == r.symbol,
                                                 m.UserTrade.status.in_(("pending", "open")))):
            raise PracticeError(f"You already have a practice trade running in {r.symbol}")
        now = repo.now_ist()
        t = m.UserTrade(report_id=r.id, symbol=r.symbol, pick_type=r.pick_type, rating=r.rating, status="pending",
                        amount=round(amount, 2), requested_at=now, stop_price=stop,
                        hold_days=rules.HOLD_DAYS.get(PickType(r.pick_type), 10), quantity=0, buy_value=0.0,
                        created_at=now, updated_at=now)
        s.add(t)
        s.flush()
        return t


def sell_user_trade(c: Container, trade_id: int) -> str:
    """Ask for a user trade to be sold at the next market open, or cancel it if it hasn't been bought yet."""
    with session_scope(c.sessions) as s:
        t = s.get(m.UserTrade, trade_id)
        if t is None:
            raise PracticeError("Trade not found")
        now = repo.now_ist()
        if t.status == "pending":
            t.status, t.skip_reason, t.updated_at = "skipped", "you cancelled it before it was bought", now
            return "Cancelled. It was never bought."
        if t.status != "open":
            raise PracticeError("This trade has already finished")
        t.sell_requested_at, t.updated_at = now, now
        return "It will be sold at the next market opening price."


# ---------------------------------------------------------------- reporting


@dataclass
class TradeView:
    t: Trade
    name: str
    group: str = capgroup.SMALL
    last_close: float | None = None
    open_net: float | None = None  # if sold at the last close, after costs and tax
    days_left: int | None = None

    @property
    def net(self) -> float | None:
        """Result in rupees after costs and tax: final if sold, otherwise as if sold at the last close."""
        return self.t.net_pnl if self.t.status == "closed" else self.open_net

    @property
    def net_pct(self) -> float | None:
        return self.net / self.t.buy_value if self.net is not None and self.t.buy_value else None

    @property
    def reason_title(self) -> str:
        return attribution.TITLE.get(self.t.reason_key or "", "")

    @property
    def price_now(self) -> float | None:
        """The price the user compares with what was paid: the sale price if sold, otherwise the latest close."""
        return self.t.exit_price if self.t.status == "closed" else self.last_close

    @property
    def price_change(self) -> float | None:
        """How much the share price moved since it was bought, before costs and tax."""
        if self.price_now is None or not self.t.entry_price:
            return None
        return self.price_now / self.t.entry_price - 1


@dataclass
class Summary:
    trades: list[TradeView] = field(default_factory=list)
    invested: float = 0.0
    realised: float = 0.0
    unrealised: float = 0.0
    index_profit: float = 0.0
    fd_profit: float = 0.0
    closed: int = 0
    winners: int = 0
    open: int = 0
    skipped: int = 0
    first_day: date | None = None
    as_of: date | None = None
    best: TradeView | None = None
    worst: TradeView | None = None
    curve: list[dict[str, Any]] = field(default_factory=list)
    by_rating: list[dict[str, Any]] = field(default_factory=list)
    verdict: rules.Verdict | None = None

    @property
    def profit(self) -> float:
        return self.realised + self.unrealised

    @property
    def profit_pct(self) -> float:
        return self.profit / self.invested if self.invested else 0.0

    @property
    def index_pct(self) -> float:
        return self.index_profit / self.invested if self.invested else 0.0

    @property
    def fd_pct(self) -> float:
        return self.fd_profit / self.invested if self.invested else 0.0

    @property
    def bought(self) -> int:
        """Picks that were actually bought (open or finished)."""
        return self.open + self.closed

    @property
    def in_profit(self) -> int:
        return sum(1 for v in self.trades if v.t.status in ("open", "closed") and (v.net or 0) > 0)


def _close_on(df: pd.DataFrame | None, day: date) -> float | None:
    if df is None or df.empty:
        return None
    upto = df[df.index.date <= day]  # type: ignore[attr-defined]
    return float(upto["close"].iloc[-1]) if not upto.empty else None


def _open_on(df: pd.DataFrame | None, day: date) -> float | None:
    if df is None or df.empty:
        return None
    rows = df[df.index.date >= day]  # type: ignore[attr-defined]
    return float(rows["open"].iloc[0]) if not rows.empty else None


def summarise(c: Container, s: Session, table: TradeTable = m.PracticeTrade) -> Summary:
    """One book's results next to the Nifty 50 and a fixed deposit, with the same money going in on the same days."""
    model = cost_model(c)
    out = Summary()
    trades: list[Trade] = list(s.scalars(select(table).order_by(table.entry_date.desc(), table.id.desc())))
    if not trades:
        return out
    stocks = {st.symbol: st for st in s.scalars(select(m.Stock).where(m.Stock.symbol.in_({t.symbol for t in trades})))}
    prices = repo.load_bars(s, sorted({t.symbol for t in trades} | {INDEX_SYMBOL}), days=400)
    index = prices.get(INDEX_SYMBOL)
    out.as_of = repo.latest_price_date(s)
    active = [t for t in trades if t.status in ("open", "closed") and t.entry_date]
    out.skipped = sum(1 for t in trades if t.status == "skipped")
    out.first_day = min((t.entry_date for t in active if t.entry_date), default=None)

    for t in trades:
        stock = stocks.get(t.symbol)
        v = TradeView(t, stock.name if stock else t.symbol, capgroup.resolve(stock.cap_group if stock else None))
        out.trades.append(v)
        if t.status not in ("open", "closed") or t.entry_date is None or t.entry_price is None:
            continue
        out.invested += t.buy_value
        end = t.exit_date or out.as_of or t.entry_date
        if t.status == "closed" and t.net_pnl is not None:
            out.realised += t.net_pnl
            out.closed += 1
            out.winners += t.net_pnl > 0
        else:
            last = _close_on(prices.get(t.symbol), end)
            if last is not None:
                v.last_close = last
                v.open_net = rules.settle(t.quantity, t.entry_price, last, model).net
                out.unrealised += v.open_net
            out.open += 1
            held = len(prices[t.symbol][prices[t.symbol].index.date >= t.entry_date]) if t.symbol in prices else 0  # type: ignore[attr-defined]
            v.days_left = max(t.hold_days - held, 0)
        idx_in, idx_out = _open_on(index, t.entry_date), _close_on(index, end)
        if idx_in and idx_out:
            out.index_profit += t.buy_value * (idx_out / idx_in - 1)
        out.fd_profit += rules.fd_interest(t.buy_value, t.entry_date, end)

    finished = [v for v in out.trades if v.t.status == "closed" and v.t.net_pnl is not None]
    if finished:
        out.best = max(finished, key=lambda v: v.t.net_pnl or 0)
        out.worst = min(finished, key=lambda v: v.t.net_pnl or 0)
    out.curve = _curve(active, prices, index, model, out.as_of)
    out.by_rating = _by_rating(finished)
    days = (out.as_of - out.first_day).days if out.as_of and out.first_day else 0
    out.verdict = rules.verdict(out.closed, days, out.profit_pct, out.index_pct)
    return out


def _curve(active: list[Trade], prices: dict[str, pd.DataFrame], index: pd.DataFrame | None,
           model: Any, as_of: date | None) -> list[dict[str, Any]]:
    """Daily profit (in rupees) for the book, the Nifty 50 and a fixed deposit, for the chart."""
    if not active or as_of is None or index is None:
        return []
    start = min(t.entry_date for t in active if t.entry_date)
    # Days come from the shares' own prices, so the chart always reaches the same date as the headline.
    stamps = set().union(*(set(pd.to_datetime(prices[t.symbol].index)) for t in active if t.symbol in prices))
    days = sorted(d.date() for d in stamps if start <= d.date() <= as_of)
    points = []
    for d in days:
        mine = idx = fd = 0.0
        for t in active:
            if t.entry_date is None or t.entry_price is None or t.entry_date > d:
                continue
            end = min(t.exit_date or d, d)
            if t.status == "closed" and t.exit_date and t.exit_date <= d and t.net_pnl is not None:
                mine += t.net_pnl
            else:
                last = _close_on(prices.get(t.symbol), d)
                if last is not None:
                    mine += rules.settle(t.quantity, t.entry_price, last, model).net
            i0, i1 = _open_on(index, t.entry_date), _close_on(index, end)
            if i0 and i1:
                idx += t.buy_value * (i1 / i0 - 1)
            fd += rules.fd_interest(t.buy_value, t.entry_date, end)
        points.append({"day": d, "mine": round(mine, 2), "index": round(idx, 2), "fd": round(fd, 2)})
    return points


def _by_rating(finished: list[TradeView]) -> list[dict[str, Any]]:
    groups = (("Very strong case (4.5 to 5)", 4.5, 9.0), ("Strong case (4 to 4.5)", 4.0, 4.5), ("Good case (3.5 to 4)", 3.5, 4.0))
    out = []
    for label, lo, hi in groups:
        g = [v for v in finished if lo <= v.t.rating < hi]
        if g:
            wins = sum(1 for v in g if (v.t.net_pnl or 0) > 0)
            out.append({"label": label, "count": len(g), "wins": wins, "net": sum(v.t.net_pnl or 0 for v in g)})
    return out


@dataclass
class Waiting:
    """A pick that has been published but not bought yet (its first opening price isn't stored)."""

    pick: m.Pick
    name: str
    group: str


def waiting_picks(s: Session) -> list[Waiting]:
    done = set(s.scalars(select(m.PracticeTrade.pick_id)))
    since = repo.today_ist() - timedelta(days=10)
    picks = [p for p in s.scalars(select(m.Pick).where(m.Pick.pick_date >= since).order_by(m.Pick.pick_date.desc()))
             if p.id not in done]
    stocks = {st.symbol: st for st in s.scalars(select(m.Stock).where(m.Stock.symbol.in_({p.symbol for p in picks})))}
    return [Waiting(p, stocks[p.symbol].name if p.symbol in stocks else p.symbol,
                    capgroup.resolve(stocks[p.symbol].cap_group if p.symbol in stocks else None)) for p in picks]


def week_summary(c: Container, s: Session, today: date) -> dict[str, Any]:
    """What the weekly email needs: the whole record, plus this week's finished and still-running picks."""
    since = today - timedelta(days=7)
    summary = summarise(c, s)
    week = [v for v in summary.trades if v.t.status == "closed" and v.t.exit_date and v.t.exit_date > since]
    running = [v for v in summary.trades if v.t.status == "open"]
    return {"summary": summary, "week": week, "running": running, "waiting": waiting_picks(s)}
