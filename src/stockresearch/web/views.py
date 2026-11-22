"""Page routes."""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text

from stockresearch import presenters
from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.domain import capgroup
from stockresearch.domain.models import PICK_TYPE_LABEL, TIER_LABEL, PickType, Tier
from stockresearch.formatting import inr
from stockresearch.services import holdings, idle, practice, rejections, repo, tracker
from stockresearch.web.auth import check_credentials, require_user, same_origin
from stockresearch.web.glossary import GLOSSARY

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")
User = Annotated[str, Depends(require_user)]
MAX_UPLOAD = 1_000_000
SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _pct(x: Any, signed: bool = True) -> str:
    if x is None:
        return "n/a"
    return f"{float(x) * 100:+.1f}%" if signed else f"{float(x) * 100:.1f}%"


def _money(x: Any) -> str:
    return inr(x, 2)



def _asset_version() -> str:
    """Short hash of the CSS and JS, added to their URLs so browsers fetch new files after each deploy."""
    static = Path(__file__).resolve().parent / "static"
    digest = hashlib.sha256()
    for name in ("style.css", "app.js"):
        digest.update((static / name).read_bytes())
    return digest.hexdigest()[:10]


templates.env.globals.update(GLOSSARY=GLOSSARY, EXIT_WORDS=presenters.TRADE_EXIT_WORDS, ASSET_V=_asset_version(),
                             GROUP_LABEL=capgroup.LABEL, GROUP_ONE=capgroup.SINGULAR, GROUP_HINT=capgroup.HINT,
                             GROUPS=capgroup.ORDER)
templates.env.filters.update(
    pct=_pct, money=_money,
    stars=lambda n: "★" * int(n) + "☆" * (5 - int(n)),
    label=lambda t: PICK_TYPE_LABEL.get(PickType(t), t) if t in {p.value for p in PickType} else t,
    tier=lambda t: TIER_LABEL.get(Tier(int(t)), "Unknown"),
    score=lambda s: "n/a" if s is None else f"{float(s):.1f}",
    rupees=inr,
    signed_rupees=lambda x: inr(x, signed=True),
    plain=presenters.plain,
    group=lambda r: capgroup.resolve(r.data.get("cap_group")),
)


def container(request: Request) -> Container:
    c: Container = request.app.state.container
    return c


def render(request: Request, template: str, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("user", request.session.get("user"))
    if ctx["user"]:
        idle.touch_visit(container(request).settings.data_dir)  # keeps the server on while you are using it
    return templates.TemplateResponse(request, template, ctx)


def check_origin(request: Request) -> None:
    if not same_origin(request):
        raise HTTPException(403, "Cross-site form submission refused")


# ---------------------------------------------------------------- health and login

@router.get("/health")
def health(request: Request) -> JSONResponse:
    c = container(request)
    try:
        with session_scope(c.sessions) as s:
            s.execute(text("SELECT 1"))
            last = {job: s.scalar(select(func.max(m.Run.finished_at)).where(m.Run.job == job, m.Run.status == "ok"))
                    for job in ("market_close", "collect", "research", "publish")}
        return JSONResponse({"status": "ok", "last_success": {k: v.isoformat() if v else None for k, v in last.items()}})
    except Exception as e:
        return JSONResponse({"status": "error", "error": type(e).__name__}, status_code=503)


@router.get("/login", response_model=None)
def login_page(request: Request, next: str = "/") -> Response:
    if request.session.get("user"):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", next=next if next.startswith("/") and not next.startswith("//") else "/",
                  configured=container(request).settings.login_configured, error=None)


@router.post("/login", response_model=None)
def login(request: Request, username: Annotated[str, Form()], password: Annotated[str, Form()],
          next: Annotated[str, Form()] = "/") -> Response:
    check_origin(request)
    c = container(request)
    key = request.client.host if request.client else "unknown"
    limiter = request.app.state.limiter
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
    if limiter.blocked(key):
        return render(request, "login.html", next=safe_next, configured=True,
                      error="Too many failed attempts. Try again in 15 minutes.")
    if not check_credentials(c.settings, username, password):
        limiter.fail(key)
        return render(request, "login.html", next=safe_next, configured=c.settings.login_configured,
                      error="Wrong username or password.")
    limiter.reset(key)
    request.session.clear()
    request.session["user"] = username
    return RedirectResponse(safe_next, status_code=303)


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    check_origin(request)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- picks and reports

@router.get("/", response_class=HTMLResponse)
def picks(request: Request, user: User, day: date | None = None) -> HTMLResponse:
    c = container(request)
    with session_scope(c.sessions) as s:
        run = s.scalar(select(m.Run).where(m.Run.job == "publish", m.Run.status == "ok",
                                           *( [func.date(m.Run.started_at) == day.isoformat()] if day else []))
                       .order_by(m.Run.started_at.desc()).limit(1))
        reports = list(s.scalars(select(m.Report).where(m.Report.run_id == run.id)
                                 .order_by(m.Report.is_pick.desc(), m.Report.rating.desc()))) if run else []
        days = [d for (d,) in s.execute(select(func.date(m.Run.started_at)).where(
            m.Run.job == "publish", m.Run.status == "ok").group_by(func.date(m.Run.started_at))
            .order_by(func.date(m.Run.started_at).desc()).limit(30))]
        alerts = list(s.scalars(select(m.Alert).order_by(m.Alert.created_at.desc()).limit(5)))
        queue = s.scalar(select(func.count()).select_from(m.ResearchQueueItem).where(
            m.ResearchQueueItem.status.in_(("pending", "running"))))
        for r in reports:
            _ = r.evidence
        bars = repo.load_bars(s, [r.symbol for r in reports], days=60) if reports else {}
        sparks = {sym: sparkline(df["close"].tail(30).tolist()) for sym, df in bars.items()}
        screen_date = s.scalar(select(func.max(m.ScreenCandidate.screen_date)))
        metrics = {
            "universe": s.scalar(select(func.count()).select_from(m.Stock).where(m.Stock.in_universe.is_(True))) or 0,
            "shortlisted": s.scalar(select(func.count()).select_from(m.ScreenCandidate)
                                    .where(m.ScreenCandidate.screen_date == screen_date)) if screen_date else 0,
            "researched": sum(1 for r in reports if r.data.get("research_at")),
        }
        amount = c.settings.practice_amount
        plains = {r.id: presenters.plain_pick(r, amount, repo.today_ist()) for r in reports}
        record = practice.summarise(c, s)
        new_picks = [r for r in reports if r.is_pick]
        groups = [{"key": g, "picks": [r for r in new_picks if capgroup.resolve(r.data.get("cap_group")) == g]}
                  for g in capgroup.ORDER]
        new_symbols = {r.symbol for r in new_picks}
        return render(request, "picks.html", run=run, picks=new_picks, groups=groups,
                      others=[r for r in reports if not r.is_pick], days=days, alerts=alerts, queue=queue,
                      ai=c.researcher is not None, min_rating=c.settings.pick_min_rating, sparks=sparks,
                      metrics=metrics, day=day, plains=plains, record=record,
                      running=[v for v in record.trades if v.t.status == "open" and v.t.symbol not in new_symbols],
                      waiting=[w for w in practice.waiting_picks(s) if w.pick.symbol not in new_symbols],
                      welcome=not request.session.get("welcomed"), amount=amount)


def sparkline(closes: list[float]) -> dict[str, str] | None:
    """Polyline points for a 200x44 SVG, plus the direction (up or down) for its colour."""
    values = [float(v) for v in closes if v == v]
    if len(values) < 2:
        return None
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = 200 / (len(values) - 1)
    points = " ".join(f"{i * step:.1f},{40 - (v - lo) / span * 36:.1f}" for i, v in enumerate(values))
    return {"line": points, "dir": "up" if values[-1] >= values[0] else "down"}


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report(request: Request, report_id: int, user: User) -> HTMLResponse:
    c = container(request)
    with session_scope(c.sessions) as s:
        r = s.get(m.Report, report_id)
        if r is None:
            raise HTTPException(404, "Report not found")
        evidence = list(r.evidence)
        pick = s.scalar(select(m.Pick).where(m.Pick.report_id == r.id))
        plain_box = presenters.plain_pick(r, c.settings.practice_amount, repo.today_ist())
        my_trade = s.scalar(select(m.UserTrade).where(m.UserTrade.symbol == r.symbol,
                                                      m.UserTrade.status.in_(("pending", "open"))))
        return render(request, "report.html", r=r, d=r.data, pick=pick, plain_box=plain_box, my_trade=my_trade,
                      amount=c.settings.practice_amount,
                      ev_for=[e for e in evidence if e.stance == "for"],
                      ev_against=[e for e in evidence if e.stance == "against"],
                      investment=c.settings.practice_amount)


@router.get("/documents/{doc_id}", response_class=HTMLResponse)
def document(request: Request, doc_id: int, user: User) -> HTMLResponse:
    c = container(request)
    with session_scope(c.sessions) as s:
        doc = s.get(m.SourceDocument, doc_id)
        if doc is None:
            raise HTTPException(404, "Document not found")
        return render(request, "document.html", doc=doc)


@router.get("/stocks/{symbol}", response_class=HTMLResponse)
def stock(request: Request, symbol: str, user: User, queued: int = 0, error: str = "") -> HTMLResponse:
    c = container(request)
    symbol = symbol.upper()
    with session_scope(c.sessions) as s:
        st = s.get(m.Stock, symbol)
        reports = list(s.scalars(select(m.Report).where(m.Report.symbol == symbol)
                                 .order_by(m.Report.created_at.desc()).limit(20)))
        pending = s.scalar(select(m.ResearchQueueItem).where(m.ResearchQueueItem.symbol == symbol,
                                                             m.ResearchQueueItem.status.in_(("pending", "running"))))
        used = repo.on_demand_today(s)
        return render(request, "stock.html", symbol=symbol, stock=st, reports=reports, pending=pending,
                      queued=queued, error=error, used=used, limit=c.settings.on_demand_daily_limit)


@router.post("/research")
def request_research(request: Request, user: User, symbol: Annotated[str, Form()]) -> RedirectResponse:
    check_origin(request)
    c = container(request)
    raw = symbol.strip().upper().removesuffix(".NS")
    with session_scope(c.sessions) as s:
        sym = holdings.resolve_symbol(s, raw) if SYMBOL_RE.match(raw) or raw.startswith("IN") else None
        if sym is None:
            return RedirectResponse(f"/stocks/{raw or 'UNKNOWN'}?error=unknown", status_code=303)
        if repo.on_demand_today(s) >= c.settings.on_demand_daily_limit:
            return RedirectResponse(f"/stocks/{sym}?error=limit", status_code=303)
        repo.enqueue(s, sym, "Requested on demand", priority=0, on_demand=True)
    return RedirectResponse(f"/stocks/{sym}?queued=1", status_code=303)


# ---------------------------------------------------------------- the app's track record

@router.get("/track-record", response_class=HTMLResponse)
def track_record(request: Request, user: User, tab: str = "picks", group: str = "all") -> HTMLResponse:
    """The app's own pretend trades on every pick, why each moved, and the stocks it said no to."""
    c = container(request)
    with session_scope(c.sessions) as s:
        summary = practice.summarise(c, s)
        picks = list(s.scalars(select(m.Pick).order_by(m.Pick.pick_date.desc(), m.Pick.rating.desc()).limit(500)))
        names = {st.symbol: st.name for st in s.scalars(select(m.Stock).where(m.Stock.symbol.in_({p.symbol for p in picks})))}
        group = group if group in capgroup.ORDER else "all"
        return render(request, "track.html", p=summary, chart=practice_chart(summary.curve),
                      waiting=practice.waiting_picks(s), rej=rejections.summarise(s),
                      tab="rejected" if tab == "rejected" else "picks", group=group,
                      picks=picks, names=names, stats=tracker.summary(picks),
                      amount=c.settings.practice_amount, investment=c.settings.reference_investment)


# ---------------------------------------------------------------- holdings

@router.get("/holdings", response_class=HTMLResponse)
def holdings_page(request: Request, user: User, msg: str = "", err: str = "") -> HTMLResponse:
    c = container(request)
    with session_scope(c.sessions) as s:
        rows = list(s.scalars(select(m.Holding).where(m.Holding.closed.is_(False)).order_by(m.Holding.symbol)))
        reviews = {}
        for h in rows:
            reviews[h.id] = s.scalar(select(m.HoldingReview).where(m.HoldingReview.holding_id == h.id)
                                     .order_by(m.HoldingReview.id.desc()).limit(1))
        alerts = list(s.scalars(select(m.Alert).where(m.Alert.holding_id.is_not(None))
                                .order_by(m.Alert.created_at.desc()).limit(20)))
        conc = holdings.portfolio_concentration(s)
        names = {st.symbol: st.name for st in s.scalars(select(m.Stock).where(m.Stock.symbol.in_({h.symbol for h in rows})))}
        actions = repo.load_actions(s, sorted({h.symbol for h in rows}))
        levels = {h.id: holdings.levels(h, actions.get(h.symbol, [])) for h in rows}
        return render(request, "holdings.html", holdings=rows, reviews=reviews, alerts=alerts, conc=conc,
                      names=names, msg=msg, err=err, today=repo.today_ist(), levels=levels)


@router.get("/holdings/{holding_id}", response_class=HTMLResponse)
def holding_detail(request: Request, holding_id: int, user: User) -> HTMLResponse:
    c = container(request)
    with session_scope(c.sessions) as s:
        h = s.get(m.Holding, holding_id)
        if h is None:
            raise HTTPException(404, "Holding not found")
        reviews = list(s.scalars(select(m.HoldingReview).where(m.HoldingReview.holding_id == h.id)
                                 .order_by(m.HoldingReview.id.desc()).limit(10)))
        alerts = list(s.scalars(select(m.Alert).where(m.Alert.holding_id == h.id).order_by(m.Alert.created_at.desc())))
        lv = holdings.levels(h, repo.load_actions(s, [h.symbol]).get(h.symbol, []))
        return render(request, "holding.html", h=h, lv=lv, name=repo.stock_name(s, h.symbol), reviews=reviews,
                      alerts=alerts)


def _float(x: str) -> float | None:
    x = (x or "").replace(",", "").strip()
    return float(x) if x else None


@router.post("/holdings")
def add_holding(request: Request, user: User, symbol: Annotated[str, Form()], quantity: Annotated[str, Form()],
                avg_price: Annotated[str, Form()], horizon: Annotated[str, Form()],
                buy_date: Annotated[str, Form()] = "", stop_price: Annotated[str, Form()] = "",
                pick_id: Annotated[str, Form()] = "") -> RedirectResponse:
    check_origin(request)
    try:
        qty, price = _float(quantity), _float(avg_price)
        if qty is None or price is None:
            raise holdings.HoldingError("Quantity and price are required")
        when = date.fromisoformat(buy_date) if buy_date else None
        holdings.add_holding(container(request), symbol, qty, price, when, horizon, _float(stop_price),
                             source="pick" if pick_id else "manual", pick_id=int(pick_id) if pick_id else None)
    except (holdings.HoldingError, ValueError) as e:
        return RedirectResponse(f"/holdings?err={str(e)[:150]}", status_code=303)
    return RedirectResponse("/holdings?msg=Holding added. It will be researched in the next run.", status_code=303)


@router.post("/holdings/import")
async def import_holdings(request: Request, user: User, file: Annotated[UploadFile, File()],
                          horizon: Annotated[str, Form()] = "long") -> RedirectResponse:
    check_origin(request)
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        return RedirectResponse("/holdings?err=File too large (1 MB limit)", status_code=303)
    try:
        result = holdings.import_csv(container(request), content.decode("utf-8-sig", errors="replace"), horizon)
    except holdings.HoldingError as e:
        return RedirectResponse(f"/holdings?err={str(e)[:200]}", status_code=303)
    msg = f"Imported {result.added} holding(s)." + (f" Skipped {len(result.skipped)}: " + "; ".join(result.skipped[:3])
                                                     if result.skipped else "")
    return RedirectResponse(f"/holdings?msg={msg[:300]}", status_code=303)


@router.post("/holdings/{holding_id}/close")
def close_holding(request: Request, holding_id: int, user: User) -> RedirectResponse:
    check_origin(request)
    with session_scope(container(request).sessions) as s:
        h = s.get(m.Holding, holding_id)
        if h is not None:
            h.closed = True
    return RedirectResponse("/holdings?msg=Holding marked as sold.", status_code=303)


# ---------------------------------------------------------------- operations

@router.get("/runs", response_class=HTMLResponse)
def runs(request: Request, user: User) -> HTMLResponse:
    c = container(request)
    with session_scope(c.sessions) as s:
        rows = list(s.scalars(select(m.Run).order_by(m.Run.started_at.desc()).limit(60)))
        queue = list(s.scalars(select(m.ResearchQueueItem).where(m.ResearchQueueItem.status.in_(("pending", "running", "failed")))
                               .order_by(m.ResearchQueueItem.priority).limit(40)))
        ai_today = repo.ai_calls_today(s)
        for r in rows:
            _ = r.stages
        return render(request, "runs.html", runs=rows, queue=queue, ai_today=ai_today,
                      ai_budget=c.settings.gemini_daily_call_budget, ai=c.researcher is not None)


@router.get("/backtest", response_class=HTMLResponse)
def backtest_page(request: Request, user: User) -> HTMLResponse:
    with session_scope(container(request).sessions) as s:
        row = s.scalar(select(m.BacktestResult).order_by(m.BacktestResult.created_at.desc()).limit(1))
        return render(request, "backtest.html", row=row, since=repo.today_ist() - timedelta(days=1))


# ---------------------------------------------------------------- practice, help, welcome

@router.get("/practice", response_class=HTMLResponse)
def practice_page(request: Request, user: User, msg: str = "", err: str = "") -> HTMLResponse:
    """My practice portfolio: pretend trades the user added themselves."""
    c = container(request)
    with session_scope(c.sessions) as s:
        summary = practice.summarise(c, s, m.UserTrade)
        return render(request, "practice.html", p=summary, chart=practice_chart(summary.curve),
                      amount=c.settings.practice_amount, msg=msg, err=err)


@router.post("/practice")
def add_practice_trade(request: Request, user: User, report_id: Annotated[int, Form()],
                       amount: Annotated[str, Form()]) -> RedirectResponse:
    check_origin(request)
    try:
        value = _float(amount)
        if value is None:
            raise practice.PracticeError("Enter an amount")
        trade = practice.add_user_trade(container(request), report_id, value)
    except (practice.PracticeError, ValueError) as e:
        return RedirectResponse(f"/practice?err={str(e)[:150]}", status_code=303)
    return RedirectResponse(f"/practice?msg=Added {trade.symbol}. It will be bought at the next market opening price.",
                            status_code=303)


@router.post("/practice/{trade_id}/sell")
def sell_practice_trade(request: Request, trade_id: int, user: User) -> RedirectResponse:
    check_origin(request)
    try:
        msg = practice.sell_user_trade(container(request), trade_id)
    except practice.PracticeError as e:
        return RedirectResponse(f"/practice?err={str(e)[:150]}", status_code=303)
    return RedirectResponse(f"/practice?msg={msg}", status_code=303)


def practice_chart(curve: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Server-drawn SVG (CSP-safe): profit in rupees over time for the portfolio, Nifty 50 and fixed deposit,
    with rupee amounts on the left axis and dates along the bottom."""
    if len(curve) < 2:
        return None
    left, right, top, bottom = 70.0, 670.0, 14.0, 186.0
    values = [p[k] for p in curve for k in ("mine", "index", "fd")] + [0.0]
    lo, hi = min(values), max(values)
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad

    def y(v: float) -> float:
        return round(top + (hi - v) / (hi - lo) * (bottom - top), 1)

    step = (right - left) / (len(curve) - 1)
    lines = {k: " ".join(f"{left + i * step:.1f},{y(p[k])}" for i, p in enumerate(curve)) for k in ("mine", "index", "fd")}
    lower = round(lo * 0.9, -2) if lo < 0 else round(hi * 0.45, -2)
    ticks = sorted({round(hi * 0.9, -2), 0.0, lower})
    y_labels = [{"y": y(t), "text": inr(t, signed=t != 0)} for t in ticks]
    mid = len(curve) // 2
    x_labels = [{"x": left, "anchor": "start", "text": curve[0]["day"].strftime("%d %b")},
                {"x": left + mid * step, "anchor": "middle", "text": curve[mid]["day"].strftime("%d %b")},
                {"x": right, "anchor": "end", "text": curve[-1]["day"].strftime("%d %b")}]
    return {"lines": lines, "zero": y(0.0), "last": curve[-1], "first_day": curve[0]["day"], "last_day": curve[-1]["day"],
            "y_labels": y_labels, "x_labels": x_labels, "left": left, "right": right, "bottom": bottom}


@router.get("/how-it-works", response_class=HTMLResponse)
def how_it_works(request: Request, user: User) -> HTMLResponse:
    c = container(request)
    return render(request, "how.html", amount=c.settings.practice_amount, min_rating=c.settings.pick_min_rating,
                  per_group=c.settings.picks_per_group)


@router.post("/welcome/dismiss")
def dismiss_welcome(request: Request, user: User) -> RedirectResponse:
    check_origin(request)
    request.session["welcomed"] = True
    return RedirectResponse("/", status_code=303)
