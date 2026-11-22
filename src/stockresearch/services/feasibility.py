"""Milestone M0: check that every data source and service is reachable from this machine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from stockresearch.adapters.nse import recent_trading_days
from stockresearch.container import Container
from stockresearch.services.repo import today_ist


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _try(name: str, fn: Callable[[], str], required: bool = True) -> Check:
    try:
        return Check(name, True, fn(), required)
    except Exception as e:
        return Check(name, False, f"{type(e).__name__}: {str(e)[:200]}", required)


def run(c: Container) -> list[Check]:
    today = today_ist()
    checks = [
        _try("NSE stock list", lambda: f"{len(c.exchange.universe())} stocks"),
        _try("NSE end-of-day file with delivery %", lambda: _bhav(c, today)),
        _try("NSE company filings", lambda: f"{len(c.exchange.announcements(today, today))} filings today"),
        _try("NSE surveillance lists", lambda: f"{len(c.exchange.surveillance())} stocks listed"),
        _try("NSE price limits", lambda: f"{len(c.exchange.price_bands())} symbols"),
        _try("NSE shareholding (sample: RELIANCE)", lambda: f"{len(c.exchange.shareholding('RELIANCE'))} quarters"),
        _try("NSE bulk and block deals", lambda: f"{len(c.exchange.bulk_and_block_deals())} deals"),
        _try("NSE corporate actions (splits, bonus issues)",
             lambda: f"{len(c.exchange.corporate_actions(today - timedelta(days=30), today))} in the last 30 days",
             required=False),
        _try("Yahoo Finance prices", lambda: f"{len(c.market.history(['RELIANCE'], period='5d'))} symbol(s)"),
        _try("Yahoo Finance financials", lambda: f"{len(c.market.fundamentals('RELIANCE').quarters)} quarters"),
        _try("Approved news search", lambda: f"{len(c.news.company_news('Reliance Industries', 7))} articles"),
        _try("Approved publisher feeds", lambda: f"{len(c.news.market_feeds())} headlines", required=False),
    ]
    if c.researcher is not None:
        checks.append(_try("Gemini API", lambda: _gemini(c)))
    else:
        checks.append(Check("Gemini API", False, "GEMINI_API_KEY not set (app runs on rules only)", required=False))
    if c.notifiers:
        for n in c.notifiers:
            checks.append(_try(f"Notifier: {n.name}", lambda n=n: _notify(n)))  # type: ignore[misc]
    else:
        checks.append(Check("Email / Telegram", False, "No notifier configured", required=False))
    return checks


def _bhav(c: Container, today) -> str:
    for day in recent_trading_days(today, 6):
        df = c.exchange.bhavcopy(day)
        if df is not None:
            share = df["delivery_pct"].notna().mean() * 100
            return f"{day}: {len(df)} rows, delivery % present on {share:.0f}%"
    raise RuntimeError("no file found for the last 6 trading days")


def _gemini(c: Container) -> str:
    from stockresearch.ports import ResearchDocument

    assert c.researcher is not None
    doc = ResearchDocument(1, "NSE", 1, str(today_ist()), "Test filing",
                           "The Company has received an order worth Rs 100 crore from a customer.")
    out = c.researcher.analyse("TEST", "Test Company Limited", {"note": "connectivity test"}, [doc])
    return f"model {c.researcher.model_name} replied; {len(out.get('events', []))} event(s) parsed"


def _notify(n) -> str:
    n.send("Stock research: connectivity test", "<p>Test message from the feasibility check.</p>",
           "Test message from the feasibility check.")
    return "test message sent"
