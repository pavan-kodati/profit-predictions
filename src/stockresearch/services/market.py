"""Market data jobs: bootstrap history, and the evening market-close job (prices, deals, surveillance, screen)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import delete, select

from stockresearch.adapters.http import SourceUnavailable
from stockresearch.adapters.nse import recent_trading_days
from stockresearch.adapters.yahoo import INDEX_SYMBOL
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import capgroup, filings, screening
from stockresearch.domain import corporate_actions as ca_rules
from stockresearch.domain.models import EventType, Tier
from stockresearch.services import corporate_actions, repo
from stockresearch.services.runs import JobContext

log = logging.getLogger(__name__)
DELIVERY_BACKFILL_DAYS = 60


# ---------------------------------------------------------------- universe

def refresh_universe(ctx: JobContext, detail: list[str]) -> None:
    c = ctx.c
    rows = c.exchange.universe()
    try:
        listed = c.exchange.all_listed()
    except SourceUnavailable as e:
        log.warning("full listing unavailable: %s", e)
        listed = []
    try:
        lists = c.exchange.cap_lists()
    except SourceUnavailable as e:
        log.warning("size lists unavailable, keeping the stored size groups: %s", e)
        lists = None
    with session_scope(c.sessions) as s:
        for r in listed:
            repo.upsert_stock(s, r.symbol, r.name, None, r.isin, in_universe=False)
        s.flush()
        for stock in s.scalars(select(m.Stock).where(m.Stock.in_universe.is_(True))):
            stock.in_universe = False
        for r in rows:
            repo.upsert_stock(s, r.symbol, r.name, r.industry, r.isin, in_universe=True)
        if lists:
            s.flush()
            for stock in s.scalars(select(m.Stock).where(m.Stock.in_universe.is_(True))):
                stock.cap_group = capgroup.from_lists(stock.symbol, lists["large"], lists["mid"])
    detail.append(f"{len(rows)} stocks in the research universe, {len(listed)} listed in total")
    detail.append(f"Size groups: {len(lists['large'])} large, {len(lists['mid'])} mid-size, the rest small" if lists
                  else "Size lists unavailable; market value is used for size groups until the next refresh")


def universe_is_stale(ctx: JobContext) -> bool:
    with session_scope(ctx.c.sessions) as s:
        newest = s.scalar(select(m.Stock.updated_at).where(m.Stock.in_universe.is_(True))
                          .order_by(m.Stock.updated_at.desc()).limit(1))
        grouped = s.scalar(select(m.Stock.symbol).where(m.Stock.in_universe.is_(True),
                                                        m.Stock.cap_group.is_not(None)).limit(1))
    return newest is None or grouped is None or newest < repo.now_ist() - timedelta(days=7)


# ---------------------------------------------------------------- prices

def _bhav_rows(df: pd.DataFrame, symbols: set[str]) -> list[dict]:
    df = df[df["symbol"].isin(symbols)].dropna(subset=["close"])
    rows = []
    for r in df.to_dict("records"):
        rows.append({
            "symbol": r["symbol"], "date": r["date"], "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"] or 0),
            "delivery_pct": None if pd.isna(r["delivery_pct"]) else float(r["delivery_pct"]),
            "series": r["series"], "source": "nse", "basis_date": r["date"],
        })
    return rows


def tracked_symbols(ctx: JobContext) -> set[str]:
    with session_scope(ctx.c.sessions) as s:
        return set(repo.universe_symbols(s)) | set(repo.holding_symbols(s))


def backfill_history(ctx: JobContext, years: int = 1) -> str:
    symbols = sorted(tracked_symbols(ctx))
    with ctx.stage("Price history from Yahoo Finance") as detail:
        frames = ctx.c.market.history([*symbols, INDEX_SYMBOL], period=f"{years}y")
        n = 0
        with session_scope(ctx.c.sessions) as s:
            for sym, df in frames.items():
                n += repo.store_prices(s, repo.frame_to_rows(sym, df, "yahoo"))
        detail.append(f"{len(frames)} symbols, {n} daily bars")
    with ctx.stage("Official NSE prices and delivery % (last 60 trading days)") as detail:
        got = 0
        for day in recent_trading_days(repo.today_ist(), DELIVERY_BACKFILL_DAYS):
            try:
                day_df = ctx.c.exchange.bhavcopy(day)
            except SourceUnavailable as e:
                detail.append(f"{day}: {e}")
                continue
            if day_df is None:
                continue
            with session_scope(ctx.c.sessions) as s:
                repo.store_prices(s, _bhav_rows(day_df, set(symbols)))
            got += 1
        detail.append(f"{got} trading days loaded")
    return f"History loaded for {len(symbols)} stocks"


def load_day_prices(ctx: JobContext, day: date, detail: list[str]) -> bool:
    """Official NSE file for `day`; Yahoo as a fallback if NSE is unreachable. Returns False on a market holiday."""
    symbols = tracked_symbols(ctx)
    try:
        df = ctx.c.exchange.bhavcopy(day)
        if df is None:
            detail.append(f"No NSE file for {day} (market holiday or not yet published)")
            return False
        with session_scope(ctx.c.sessions) as s:
            jumps = corporate_actions.cross_check(s, df, day, symbols)
            n = repo.store_prices(s, _bhav_rows(df, symbols))
        detail.append(f"{n} stocks from the official NSE file")
        if jumps:
            detail.append(f"Unexplained change in share count (prices adjusted, kept out of picks): {', '.join(jumps)}")
    except SourceUnavailable as e:
        detail.append(f"NSE unavailable ({e}); falling back to Yahoo Finance")
        frames = ctx.c.market.history(sorted(symbols), period="5d")
        with session_scope(ctx.c.sessions) as s:
            for sym, f in frames.items():
                repo.store_prices(s, repo.frame_to_rows(sym, f, "yahoo"))
        detail.append(f"{len(frames)} stocks from Yahoo")
        if not frames:
            raise
    frames = ctx.c.market.history([INDEX_SYMBOL], period="5d")
    with session_scope(ctx.c.sessions) as s:
        for sym, f in frames.items():
            repo.store_prices(s, repo.frame_to_rows(sym, f, "yahoo"))
    return True


# ---------------------------------------------------------------- deals and surveillance

def ingest_deals(ctx: JobContext, detail: list[str]) -> None:
    deals = ctx.c.exchange.bulk_and_block_deals()
    tracked = tracked_symbols(ctx)
    stored = events = 0
    with session_scope(ctx.c.sessions) as s:
        for d in deals:
            if d.symbol not in tracked:
                continue
            institution = filings.is_institution(d.client)
            verb = "bought" if d.side == "BUY" else "sold"
            text = (f"On {d.deal_date:%d %b %Y}, {d.client} {verb} {d.quantity:,.0f} shares of {d.symbol} at "
                    f"Rs {d.price:,.2f} ({d.kind} deal, value Rs {d.quantity * d.price / 1e7:,.1f} crore).")
            url = (f"https://nsearchives.nseindia.com/content/equities/{d.kind}.csv"
                   f"#{d.deal_date:%Y%m%d}-{repo.slug(d.client, 40)}-{d.side}-{int(d.quantity)}")
            doc = repo.save_document(
                s, url=url, symbol=d.symbol, kind="deal", category="institution" if institution else "other",
                title=f"{d.kind.title()} deal: {d.client} {verb} {d.symbol}", text=text,
                published_at=datetime.combine(d.deal_date, datetime.min.time()), tier=Tier.OFFICIAL, publisher="NSE",
            )
            stored += 1
            if institution and d.side == "BUY":
                repo.upsert_event(s, key=f"deal:{url}", symbol=d.symbol, event_type=EventType.STAKE_BUY, polarity=1,
                                  title=f"{d.client} bought shares in a {d.kind} deal", event_date=d.deal_date,
                                  documents=[doc], origin="rule", value_cr=round(d.quantity * d.price / 1e7, 2))
                events += 1
    detail.append(f"{len(deals)} deals on NSE, {stored} on tracked stocks, {events} institutional purchases")


def ingest_surveillance(ctx: JobContext, detail: list[str]) -> None:
    entries = ctx.c.exchange.surveillance()
    with session_scope(ctx.c.sessions) as s:
        repo.replace_surveillance(s, entries, repo.today_ist())
    detail.append(f"{len(entries)} stocks on ASM/GSM surveillance lists")


def ingest_price_bands(ctx: JobContext, detail: list[str]) -> None:
    bands = ctx.c.exchange.price_bands()
    with session_scope(ctx.c.sessions) as s:
        n = repo.update_price_bands(s, bands)
    detail.append(f"Official price limits for {n} tracked stocks")


# ---------------------------------------------------------------- screen

def run_screen(ctx: JobContext, detail: list[str]) -> None:
    c = ctx.c
    today = repo.today_ist()
    with session_scope(c.sessions) as s:
        stocks = list(s.scalars(select(m.Stock).where(m.Stock.in_universe.is_(True))))
        groups: dict[str, str] | None = {st.symbol: capgroup.resolve(st.cap_group) for st in stocks}
        symbols = sorted(st.symbol for st in stocks)
        if not any(st.cap_group for st in stocks):
            groups = None  # size lists never loaded: one shortlist for everyone, sized like three groups
        bars = repo.load_bars(s, symbols, days=420)
        positive_events = set(s.scalars(select(m.Event.symbol).where(
            m.Event.polarity > 0, m.Event.status == "verified", m.Event.event_date >= today - timedelta(days=5))))
        surveillance = set(s.scalars(select(m.Surveillance.symbol)))
        unreliable = {sym for sym, acts in repo.load_actions(s, symbols).items()
                      if ca_rules.price_warning(acts, today)}
    techs = {}
    for sym, df in bars.items():
        t = repo.technicals_for(df)
        if t is not None and sym not in surveillance and sym not in unreliable:
            techs[sym] = t
    per_group_limit = c.settings.shortlist_per_group * (1 if groups is not None else len(capgroup.ORDER))
    candidates = screening.screen(techs, positive_events, c.settings.min_turnover_cr, per_group_limit, groups)
    as_of = max((t.as_of for t in techs.values()), default=today)
    with session_scope(c.sessions) as s:
        s.execute(delete(m.ScreenCandidate).where(m.ScreenCandidate.screen_date == as_of))
        for i, cand in enumerate(candidates):
            s.add(m.ScreenCandidate(screen_date=as_of, symbol=cand.symbol, pre_score=cand.pre_score, reason=cand.reason))
            repo.enqueue(s, cand.symbol, f"Screen: {cand.reason}", priority=30 + i)
        for sym in repo.holding_symbols(s):
            last = repo.latest_research(s, sym, max_age_days=7)
            if last is None:
                repo.enqueue(s, sym, "Weekly refresh of a holding", priority=50)
    if groups is None:
        detail.append(f"{len(techs)} stocks with enough history; {len(candidates)} shortlisted (size groups unknown)")
    else:
        per_group = {g: sum(1 for cand in candidates if groups.get(cand.symbol) == g) for g in capgroup.ORDER}
        detail.append(f"{len(techs)} stocks with enough history; {len(candidates)} shortlisted for research "
                      f"({per_group['large']} large, {per_group['mid']} mid-size, {per_group['small']} small)")


def market_close(ctx: JobContext, day: date | None = None) -> str:
    from stockresearch.services import holdings, practice, rejections, tracker

    day = day or repo.today_ist()
    if universe_is_stale(ctx):
        with ctx.stage("Refresh NSE stock list") as detail:
            refresh_universe(ctx, detail)
    ctx.soft_stage("Splits and bonus issues",
                   lambda d: corporate_actions.ingest(ctx.c, tracked_symbols(ctx), d, today=day))
    with ctx.stage("Official end-of-day prices") as detail:
        traded = load_day_prices(ctx, day, detail)
    if not traded:
        return f"No trading on {day}; nothing else to do"
    ctx.soft_stage("Surveillance lists", lambda d: ingest_surveillance(ctx, d))
    ctx.soft_stage("Price limits", lambda d: ingest_price_bands(ctx, d))
    ctx.soft_stage("Bulk and block deals", lambda d: ingest_deals(ctx, d))
    with ctx.stage("Screen the market") as detail:
        run_screen(ctx, detail)
    ctx.soft_stage("Track record of earlier picks", lambda d: tracker.update_outcomes(ctx.c, d))
    ctx.soft_stage("Pretend trades (app's track record and practice portfolio)", lambda d: practice.sync(ctx.c, d))
    ctx.soft_stage("Stocks the app said no to", lambda d: rejections.update(ctx.c, d))
    ctx.soft_stage("Exit alerts for holdings", lambda d: holdings.exit_alerts(ctx.c, d))
    return f"Market close for {day} done"
