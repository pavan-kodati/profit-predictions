"""Wires adapters to ports from settings. Tests build a Container with fakes instead."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from stockresearch.config import Settings, get_settings
from stockresearch.ports import ExchangeSource, MarketDataSource, NewsSource, Notifier, Researcher


@dataclass
class Container:
    settings: Settings
    sessions: sessionmaker[Session]
    exchange: ExchangeSource
    market: MarketDataSource
    news: NewsSource
    researcher: Researcher | None
    notifiers: list[Notifier] = field(default_factory=list)


def build() -> Container:
    from stockresearch.adapters.gemini import GeminiResearcher
    from stockresearch.adapters.news import AllowlistedNews
    from stockresearch.adapters.notifiers import SmtpNotifier, TelegramNotifier
    from stockresearch.adapters.nse import NseExchange
    from stockresearch.adapters.yahoo import YahooMarketData
    from stockresearch.db.session import get_sessionmaker

    s = get_settings()
    notifiers: list[Notifier] = []
    if s.email_enabled:
        notifiers.append(SmtpNotifier(s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password.get_secret_value(), s.email_to))
    if s.telegram_enabled:
        notifiers.append(TelegramNotifier(s.telegram_bot_token.get_secret_value(), s.telegram_chat_id))
    researcher = GeminiResearcher(s.gemini_api_key.get_secret_value(), s.gemini_model) if s.ai_enabled else None
    return Container(
        settings=s,
        sessions=get_sessionmaker(),
        exchange=NseExchange(),
        market=YahooMarketData(),
        news=AllowlistedNews(),
        researcher=researcher,
        notifiers=notifiers,
    )
