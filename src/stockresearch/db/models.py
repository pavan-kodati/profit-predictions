"""Database tables (SQLAlchemy 2). Schema changes go through Alembic migrations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Stock(Base):
    __tablename__ = "stocks"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str | None] = mapped_column(String(120))
    isin: Mapped[str | None] = mapped_column(String(12), index=True)
    in_universe: Mapped[bool] = mapped_column(Boolean, default=False)
    price_band: Mapped[float | None] = mapped_column(Float)  # daily limit in %, 0 = no fixed band, None = unknown
    cap_group: Mapped[str | None] = mapped_column(String(8))  # large | mid | small, from NSE index lists
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    delivery_pct: Mapped[float | None] = mapped_column(Float)
    series: Mapped[str | None] = mapped_column(String(4))
    source: Mapped[str] = mapped_column(String(16))


class FundamentalsSnapshot(Base):
    __tablename__ = "fundamentals_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class QuarterlyResult(Base):
    __tablename__ = "fundamentals_quarterly"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    revenue_cr: Mapped[float | None] = mapped_column(Float)
    profit_cr: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))


class ShareholdingQuarterly(Base):
    __tablename__ = "shareholding_quarterly"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    promoter_pct: Mapped[float | None] = mapped_column(Float)
    public_pct: Mapped[float | None] = mapped_column(Float)
    pledged_pct: Mapped[float | None] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(String(500))


class ScreenCandidate(Base):
    """Stocks shortlisted by the evening screen; the morning publish rates these."""

    __tablename__ = "screen_candidates"
    screen_date: Mapped[date] = mapped_column(Date, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    pre_score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(300))


class Surveillance(Base):
    __tablename__ = "surveillance"
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    list_name: Mapped[str] = mapped_column(String(16), primary_key=True)
    stage: Mapped[str | None] = mapped_column(String(80))
    as_of: Mapped[date] = mapped_column(Date)


event_sources = Table(
    "event_sources",
    Base.metadata,
    Column("event_id", ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
    Column("document_id", ForeignKey("source_documents.id", ondelete="CASCADE"), primary_key=True),
)


class SourceDocument(Base):
    """Every filing or article the app has read, with a saved copy of its text."""

    __tablename__ = "source_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # filing | article | deal | insider | shareholding
    category: Mapped[str | None] = mapped_column(String(200))
    publisher: Mapped[str] = mapped_column(String(120))
    tier: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_symbol_date", "symbol", "event_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    symbol: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(32))
    polarity: Mapped[int] = mapped_column(Integer)  # +1 positive, -1 negative, 0 neutral / unknown
    title: Mapped[str] = mapped_column(Text)
    event_date: Mapped[date] = mapped_column(Date)
    value_cr: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16))  # verified | reported | unverified
    origin: Mapped[str] = mapped_column(String(16))  # rule | ai
    created_at: Mapped[datetime] = mapped_column(DateTime)
    documents: Mapped[list[SourceDocument]] = relationship(secondary=event_sources, lazy="selectin")


class ResearchQueueItem(Base):
    __tablename__ = "research_queue"
    __table_args__ = (Index("ix_queue_status_priority", "status", "priority"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(Integer)  # lower runs first
    on_demand: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(Text)


class ResearchResult(Base):
    """Output of one research pass on one stock: structured data plus validated AI findings."""

    __tablename__ = "research_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ai_used: Mapped[bool] = mapped_column(Boolean)
    model: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16))  # running | ok | failed | skipped
    notes: Mapped[str] = mapped_column(Text, default="")
    stages: Mapped[list[RunStage]] = relationship(back_populates="run", lazy="selectin", order_by="RunStage.id")


class RunStage(Base):
    __tablename__ = "run_stages"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text, default="")
    run: Mapped[Run] = relationship(back_populates="stages")


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_symbol_created", "symbol", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    data_as_of: Mapped[date | None] = mapped_column(Date)
    purpose: Mapped[str] = mapped_column(String(16))  # daily | on_demand | holding
    rating: Mapped[float] = mapped_column(Float)
    stars: Mapped[int] = mapped_column(Integer)
    pick_type: Mapped[str] = mapped_column(String(16))
    completeness: Mapped[float] = mapped_column(Float)
    is_pick: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded_reason: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence: Mapped[list[EvidenceRow]] = relationship(back_populates="report", lazy="selectin",
                                                       cascade="all, delete-orphan")


class EvidenceRow(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("source_documents.id"))
    area: Mapped[str] = mapped_column(String(16))
    stance: Mapped[str] = mapped_column(String(8))  # for | against
    claim: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1000))
    publisher: Mapped[str] = mapped_column(String(120))
    tier: Mapped[int] = mapped_column(Integer)
    trust_label: Mapped[str] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    report: Mapped[Report] = relationship(back_populates="evidence")


class Pick(Base):
    __tablename__ = "picks"
    __table_args__ = (UniqueConstraint("symbol", "pick_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    pick_date: Mapped[date] = mapped_column(Date, index=True)
    ref_close: Mapped[float] = mapped_column(Float)
    ref_date: Mapped[date] = mapped_column(Date)
    rating: Mapped[float] = mapped_column(Float)
    pick_type: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[PickOutcome | None] = relationship(lazy="selectin", uselist=False, cascade="all, delete-orphan")


class PickOutcome(Base):
    __tablename__ = "pick_outcomes"
    pick_id: Mapped[int] = mapped_column(ForeignKey("picks.id", ondelete="CASCADE"), primary_key=True)
    entry_price: Mapped[float | None] = mapped_column(Float)
    entry_date: Mapped[date | None] = mapped_column(Date)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    ret_5: Mapped[float | None] = mapped_column(Float)
    ret_10: Mapped[float | None] = mapped_column(Float)
    ret_15: Mapped[float | None] = mapped_column(Float)
    net_ret_10: Mapped[float | None] = mapped_column(Float)
    max_gain: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    days_tracked: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class PracticeTrade(Base):
    """One pretend trade in the practice portfolio. Append-only: rules changes create a new rules_version."""

    __tablename__ = "practice_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    pick_id: Mapped[int] = mapped_column(ForeignKey("picks.id"), unique=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    rules_version: Mapped[int] = mapped_column(Integer)
    pick_date: Mapped[date] = mapped_column(Date, index=True)
    pick_type: Mapped[str] = mapped_column(String(16))
    rating: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(8))  # open | closed | skipped
    skip_reason: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float)  # money set aside for this pick
    entry_date: Mapped[date | None] = mapped_column(Date)
    entry_price: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    buy_value: Mapped[float] = mapped_column(Float, default=0.0)
    stop_price: Mapped[float] = mapped_column(Float)
    hold_days: Mapped[int] = mapped_column(Integer)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(16))
    charges: Mapped[float | None] = mapped_column(Float)
    tax: Mapped[float | None] = mapped_column(Float)
    net_pnl: Mapped[float | None] = mapped_column(Float)
    reason_key: Mapped[str | None] = mapped_column(String(16))  # why it moved: see domain/attribution.py
    reason_text: Mapped[str | None] = mapped_column(Text)
    reason_links: Mapped[list[Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class UserTrade(Base):
    """A pretend trade the user added themselves (My practice portfolio). Kept apart from the app's own record."""

    __tablename__ = "user_trades"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    pick_type: Mapped[str] = mapped_column(String(16))
    rating: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(8))  # pending | open | closed | skipped
    skip_reason: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float)
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    sell_requested_at: Mapped[datetime | None] = mapped_column(DateTime)
    entry_date: Mapped[date | None] = mapped_column(Date)
    entry_price: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    buy_value: Mapped[float] = mapped_column(Float, default=0.0)
    stop_price: Mapped[float] = mapped_column(Float)
    hold_days: Mapped[int] = mapped_column(Integer)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_reason: Mapped[str | None] = mapped_column(String(16))
    charges: Mapped[float | None] = mapped_column(Float)
    tax: Mapped[float | None] = mapped_column(Float)
    net_pnl: Mapped[float | None] = mapped_column(Float)
    reason_key: Mapped[str | None] = mapped_column(String(16))
    reason_text: Mapped[str | None] = mapped_column(Text)
    reason_links: Mapped[list[Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    @property
    def pick_date(self) -> date:
        return self.requested_at.date()


class Rejection(Base):
    """A stock the app researched and decided not to pick, followed afterwards to see if saying no was right."""

    __tablename__ = "rejections"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), unique=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    reject_date: Mapped[date] = mapped_column(Date, index=True)
    ref_close: Mapped[float] = mapped_column(Float)
    ref_date: Mapped[date] = mapped_column(Date)
    rating: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    ret: Mapped[float | None] = mapped_column(Float)  # price change since the rejection
    index_ret: Mapped[float | None] = mapped_column(Float)  # Nifty 50 change over the same days
    days_tracked: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str | None] = mapped_column(String(16))  # right | little | missed
    final: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Holding(Base):
    __tablename__ = "holdings"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)
    buy_date: Mapped[date | None] = mapped_column(Date)
    horizon: Mapped[str] = mapped_column(String(8))  # short | long
    stop_price: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16))  # manual | import | pick
    pick_id: Mapped[int | None] = mapped_column(ForeignKey("picks.id"))
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class HoldingReview(Base):
    __tablename__ = "holding_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    holding_id: Mapped[int] = mapped_column(ForeignKey("holdings.id", ondelete="CASCADE"), index=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"))
    review_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))  # intact | weakening | broken
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    holding_id: Mapped[int | None] = mapped_column(ForeignKey("holdings.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))  # info | warn | exit
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)


class AiUsage(Base):
    __tablename__ = "ai_usage"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)


class BacktestResult(Base):
    __tablename__ = "backtest_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
