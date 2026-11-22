"""News from approved outlets only.

Two inputs, both filtered through the allowlist in domain.sources before anything is stored:
1. Per-company search on Google News, restricted with site: filters to the approved press domains. Google News is
   only used as an index; the publisher of each item is checked again against the allowlist.
2. Market-wide RSS feeds published directly by approved outlets.
"""

from __future__ import annotations

import calendar
import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote_plus

import feedparser

from stockresearch.adapters.http import make_session
from stockresearch.domain.sources import allowlisted_press_sites
from stockresearch.ports import Article

log = logging.getLogger(__name__)

PUBLISHER_FEEDS = (
    ("https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms", "https://economictimes.indiatimes.com"),
    ("https://www.livemint.com/rss/markets", "https://www.livemint.com"),
    ("https://www.business-standard.com/rss/markets-106.rss", "https://www.business-standard.com"),
    ("https://www.thehindubusinessline.com/markets/stock-markets/feeder/default.rss", "https://www.thehindubusinessline.com"),
    ("https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml", "https://www.cnbctv18.com"),
)
_TAGS = re.compile(r"<[^>]+>")


def short_name(name: str) -> str:
    return re.sub(r"\b(Limited|Ltd\.?|Pvt\.?|Private)\s*$", "", name or "", flags=re.I).strip(" .,")


def _when(entry: feedparser.FeedParserDict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC).replace(tzinfo=None)


class AllowlistedNews:
    def __init__(self) -> None:
        self.http = make_session(min_interval=1.0)

    def _feed(self, url: str) -> feedparser.FeedParserDict:
        r = self.http.get(url)
        r.raise_for_status()
        return feedparser.parse(r.content)

    def company_news(self, name: str, days: int = 14) -> list[Article]:
        sites = " OR ".join(f"site:{d}" for d in allowlisted_press_sites())
        query = quote_plus(f'"{short_name(name)}" ({sites}) when:{days}d')
        try:
            feed = self._feed(f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en")
        except Exception as exc:
            log.warning("news search failed for %s: %s", name, exc)
            return []
        out = []
        for e in feed.entries[:40]:
            source = e.get("source") or {}
            publisher_url = source.get("href", "") if hasattr(source, "get") else ""
            title = e.get("title", "")
            source_title = source.get("title", "") if hasattr(source, "get") else ""
            if source_title and title.endswith(f" - {source_title}"):
                title = title[: -len(source_title) - 3]
            out.append(Article(url=e.get("link", ""), publisher_url=publisher_url, title=title.strip(),
                               published_at=_when(e), summary=""))
        return out

    def market_feeds(self) -> list[Article]:
        out = []
        for feed_url, publisher_url in PUBLISHER_FEEDS:
            try:
                feed = self._feed(feed_url)
            except Exception as exc:
                log.warning("publisher feed failed %s: %s", feed_url, exc)
                continue
            for e in feed.entries:
                link = e.get("link", "")
                summary = _TAGS.sub(" ", e.get("summary", "") or "").strip()
                out.append(Article(url=link, publisher_url=link or publisher_url, title=e.get("title", "").strip(),
                                   published_at=_when(e), summary=summary[:1000]))
        return out
