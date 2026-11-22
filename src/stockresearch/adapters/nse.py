"""NSE adapter: official end-of-day files, filings, deals, surveillance lists, shareholding, pledges, insider trades.

NSE's website APIs need a cookie from the home page first. NSE may block some cloud IP ranges; every method raises
SourceUnavailable (or returns an empty result where that is safe) so the pipeline can degrade instead of crash.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from pypdf import PdfReader

from stockresearch.adapters.http import SourceUnavailable, make_session
from stockresearch.ports import (
    CorporateActionRow,
    Deal,
    Filing,
    InsiderTrade,
    PledgeInfo,
    ShareholdingRow,
    UniverseRow,
)

log = logging.getLogger(__name__)

HOME = "https://www.nseindia.com"
API = "https://www.nseindia.com/api"
ARCHIVES = "https://nsearchives.nseindia.com"
UNIVERSE_URLS = (
    f"{ARCHIVES}/content/indices/ind_niftytotalmarket_list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv",
    f"{ARCHIVES}/content/indices/ind_nifty500list.csv",
)
# Official index lists that decide a company's size group: Nifty 100 = large, Nifty Midcap 150 = mid-size.
CAP_LIST_URLS = {
    "large": (f"{ARCHIVES}/content/indices/ind_nifty100list.csv",
              "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv"),
    "mid": (f"{ARCHIVES}/content/indices/ind_niftymidcap150list.csv",
            "https://www.niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv"),
}
MAX_PDF_BYTES = 6_000_000


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        s = str(x).replace(",", "").strip()
        if s in {"", "-", "NA", "N.A."}:
            return None
        return float(s)
    except ValueError:
        return None


def _dt(text: str | None, *formats: str) -> datetime | None:
    if not text:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


class NseExchange:
    def __init__(self) -> None:
        self.http = make_session(min_interval=0.6)
        self._primed = False

    # ------------------------------------------------------------ plumbing

    def _prime(self) -> None:
        if not self._primed:
            try:
                self.http.get(HOME, timeout=20)
            except Exception as e:
                raise SourceUnavailable(f"NSE home page unreachable: {e}") from e
            self._primed = True

    def _api(self, path: str, **params: Any) -> Any:
        self._prime()
        try:
            r = self.http.get(f"{API}/{path}", params=params, headers={"Referer": HOME + "/"})
            if r.status_code in (401, 403):
                self._primed = False
                self._prime()
                r = self.http.get(f"{API}/{path}", params=params, headers={"Referer": HOME + "/"})
            r.raise_for_status()
            return r.json()
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"NSE API {path} failed: {e}") from e

    def _csv(self, url: str) -> pd.DataFrame | None:
        try:
            r = self.http.get(url)
        except Exception as e:
            raise SourceUnavailable(f"{url}: {e}") from e
        if r.status_code == 404:
            return None
        if not r.ok:
            raise SourceUnavailable(f"{url}: HTTP {r.status_code}")
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [str(c).strip() for c in df.columns]
        return df

    # ------------------------------------------------------------ lists

    def universe(self) -> list[UniverseRow]:
        last_error: Exception | None = None
        for url in UNIVERSE_URLS:
            try:
                df = self._csv(url)
            except SourceUnavailable as e:
                last_error = e
                continue
            if df is not None and {"Symbol", "Company Name"} <= set(df.columns) and len(df) > 100:
                return [
                    UniverseRow(str(r["Symbol"]).strip(), str(r["Company Name"]).strip(),
                                str(r.get("Industry") or "") or None, str(r.get("ISIN Code") or "") or None)
                    for _, r in df.iterrows()
                ]
        raise SourceUnavailable(f"Could not download the NSE stock list: {last_error}")

    def cap_lists(self) -> dict[str, set[str]]:
        """Symbols in the Nifty 100 ("large") and the Nifty Midcap 150 ("mid")."""
        out: dict[str, set[str]] = {}
        for group, urls in CAP_LIST_URLS.items():
            last_error: Exception | None = None
            for url in urls:
                try:
                    df = self._csv(url)
                except SourceUnavailable as e:
                    last_error = e
                    continue
                if df is not None and "Symbol" in df.columns and len(df) >= 50:
                    out[group] = {str(x).strip() for x in df["Symbol"]}
                    break
            if group not in out:
                raise SourceUnavailable(f"Could not download the NSE {group}-company list: {last_error}")
        return out

    def all_listed(self) -> list[UniverseRow]:
        df = self._csv(f"{ARCHIVES}/content/equities/EQUITY_L.csv")
        if df is None:
            return []
        return [
            UniverseRow(str(r["SYMBOL"]).strip(), str(r["NAME OF COMPANY"]).strip(), None,
                        str(r.get("ISIN NUMBER") or "").strip() or None)
            for _, r in df.iterrows()
        ]

    # ------------------------------------------------------------ prices

    def bhavcopy(self, day: date) -> pd.DataFrame | None:
        """Official end-of-day prices with delivery %, or None if there was no trading that day."""
        df = self._csv(f"{ARCHIVES}/products/content/sec_bhavdata_full_{day:%d%m%Y}.csv")
        if df is None or df.empty:
            return None
        # On market holidays NSE serves the previous session's file under the holiday's name. Trust the date inside.
        file_dates = {d for d in (_dt(str(x), "%d-%b-%Y") for x in df["DATE1"].astype(str).str.strip().unique()) if d}
        if file_dates and day not in {d.date() for d in file_dates}:
            log.info("NSE file for %s contains another session (%s); treating %s as a holiday",
                     day, ", ".join(sorted(str(d.date()) for d in file_dates)), day)
            return None
        df = df.rename(columns={
            "SYMBOL": "symbol", "SERIES": "series", "OPEN_PRICE": "open", "HIGH_PRICE": "high",
            "LOW_PRICE": "low", "CLOSE_PRICE": "close", "TTL_TRD_QNTY": "volume", "DELIV_PER": "delivery_pct",
            "PREV_CLOSE": "prev_close",  # NSE adjusts this on an ex-date: used to catch splits we don't know about
        })
        df["series"] = df["series"].astype(str).str.strip()
        df["symbol"] = df["symbol"].astype(str).str.strip()
        df = df[df["series"].isin(["EQ", "BE", "BZ", "SM", "ST"])].copy()
        if "prev_close" not in df:
            df["prev_close"] = None
        for col in ("open", "high", "low", "close", "volume", "delivery_pct", "prev_close"):
            df[col] = pd.to_numeric(df[col].astype(str).str.strip().replace("-", None), errors="coerce")
        df["date"] = day
        return df[["symbol", "series", "date", "open", "high", "low", "close", "volume", "delivery_pct", "prev_close"]]

    # ------------------------------------------------------------ filings and deals

    def announcements(self, start: date, end: date, symbol: str | None = None) -> list[Filing]:
        params: dict[str, Any] = {"index": "equities", "from_date": f"{start:%d-%m-%Y}", "to_date": f"{end:%d-%m-%Y}"}
        if symbol:
            params["symbol"] = symbol
        rows = self._api("corporate-announcements", **params) or []
        out = []
        for x in rows:
            published = _dt(x.get("an_dt"), "%d-%b-%Y %H:%M:%S") or _dt(x.get("sort_date"), "%Y-%m-%d %H:%M:%S")
            url = x.get("attchmntFile") or ""
            if not published or not url or not x.get("symbol"):
                continue
            out.append(Filing(
                symbol=str(x["symbol"]).strip(), company=str(x.get("sm_name") or ""),
                category=str(x.get("desc") or "").strip(), summary=str(x.get("attchmntText") or "").strip(),
                url=url, published_at=published,
            ))
        return out

    def corporate_actions(self, start: date, end: date) -> list[CorporateActionRow]:
        """Corporate actions with an ex-date between `start` and `end` (dividends included; the caller filters)."""
        rows = self._api("corporates-corporateActions", index="equities",
                         from_date=f"{start:%d-%m-%Y}", to_date=f"{end:%d-%m-%Y}") or []
        return parse_corporate_actions(rows)

    def bulk_and_block_deals(self) -> list[Deal]:
        out: list[Deal] = []
        for kind in ("bulk", "block"):
            df = self._csv(f"{ARCHIVES}/content/equities/{kind}.csv")
            if df is None:
                continue
            price_col = next((c for c in df.columns if c.startswith("Trade Price")), None)
            for _, r in df.iterrows():
                d = _dt(str(r.get("Date", "")), "%d-%b-%Y")
                if not d or not price_col:
                    continue
                out.append(Deal(
                    symbol=str(r["Symbol"]).strip(), deal_date=d.date(), client=str(r["Client Name"]).strip(),
                    side=str(r["Buy/Sell"]).strip().upper(), quantity=_num(r["Quantity Traded"]) or 0.0,
                    price=_num(r[price_col]) or 0.0, kind=kind,
                ))
        return out

    def price_bands(self) -> dict[str, float]:
        """Daily price limit per symbol in percent; 0 means no fixed band (derivatives stocks)."""
        df = self._csv(f"{ARCHIVES}/content/equities/sec_list.csv")
        if df is None:
            return {}
        out: dict[str, float] = {}
        for r in df.to_dict("records"):
            if str(r.get("Series", "")).strip() not in {"EQ", "BE", "BZ", "SM", "ST"}:
                continue
            band = str(r.get("Band", "")).strip()
            value = 0.0 if band.lower() == "no band" else _num(band)
            if value is not None:
                out[str(r["Symbol"]).strip()] = value
        return out

    def surveillance(self) -> dict[str, str]:
        out: dict[str, str] = {}
        asm = self._api("reportASM") or {}
        for term in ("longterm", "shortterm"):
            for x in (asm.get(term) or {}).get("data", []):
                out[str(x["symbol"]).strip()] = str(x.get("survDesc") or x.get("survCode") or "ASM")
        for x in self._api("reportGSM") or []:
            out[str(x["symbol"]).strip()] = str(x.get("survDesc") or "GSM")
        return out

    # ------------------------------------------------------------ per-company

    def shareholding(self, symbol: str) -> list[ShareholdingRow]:
        rows = self._api("corporate-share-holdings-master", index="equities", symbol=symbol) or []
        out = []
        for x in rows:
            d = _dt(x.get("date"), "%d-%b-%Y")
            if d:
                out.append(ShareholdingRow(d.date(), _num(x.get("pr_and_prgrp")), _num(x.get("public_val")), x.get("xbrl")))
        out.sort(key=lambda r: r.period_end, reverse=True)
        return out

    def pledge(self, symbol: str) -> PledgeInfo:
        data = (self._api("corporate-pledgedata", index="equities", symbol=symbol) or {}).get("data") or []
        if not data:
            return PledgeInfo(None, None)
        x = data[0]
        d = _dt(x.get("shp"), "%d-%b-%Y")
        return PledgeInfo(d.date() if d else None, _num(x.get("percSharesPledged")))

    def insider_trades(self, symbol: str) -> list[InsiderTrade]:
        data = (self._api("corporates-pit", index="equities", symbol=symbol) or {}).get("data") or []
        out = []
        for x in data:
            if str(x.get("secType", "")).lower() != "equity shares":
                continue
            side = str(x.get("tdpTransactionType") or "").lower()
            if side not in {"buy", "sell"}:
                continue
            d = _dt(x.get("acqfromDt"), "%d-%b-%Y") or _dt(x.get("date"), "%d-%b-%Y %H:%M")
            value = _num(x.get("secVal")) or 0.0
            if not d:
                continue
            out.append(InsiderTrade(str(x.get("acqName") or ""), str(x.get("personCategory") or ""), side, value,
                                    d.date(), x.get("xbrl")))
        return out

    def next_results_date(self, symbol: str, today: date) -> date | None:
        rows = self._api("corporate-board-meetings", index="equities", symbol=symbol) or []
        upcoming = []
        for x in rows:
            d = _dt(x.get("bm_date"), "%d-%b-%Y")
            text = f"{x.get('bm_purpose', '')} {x.get('bm_desc', '')}".lower()
            if d and d.date() >= today and "result" in text:
                upcoming.append(d.date())
        return min(upcoming) if upcoming else None

    def document_text(self, url: str, max_pages: int = 6) -> str:
        """Text of an exchange PDF attachment (first pages only). Empty string if unreadable."""
        if not url.lower().endswith(".pdf"):
            return ""
        try:
            r = self.http.get(url, stream=True)
            r.raise_for_status()
            content = r.raw.read(MAX_PDF_BYTES + 1, decode_content=True)
            if len(content) > MAX_PDF_BYTES:
                log.info("PDF too large, skipped: %s", url)
                return ""
            reader = PdfReader(io.BytesIO(content))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
            return re.sub(r"[ \t]+", " ", text).strip()
        except Exception as e:
            log.info("could not read PDF %s: %s", url, e)
            return ""


def parse_corporate_actions(rows: list[dict[str, Any]]) -> list[CorporateActionRow]:
    out = []
    for x in rows:
        ex = _dt(str(x.get("exDate") or ""), "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d")
        symbol, subject = str(x.get("symbol") or "").strip(), " ".join(str(x.get("subject") or "").split())
        if ex and symbol and subject and str(x.get("series") or "EQ").strip() in {"EQ", "BE", "BZ", "SM", "ST", ""}:
            out.append(CorporateActionRow(symbol, ex.date(), subject))
    return out


def recent_trading_days(end: date, count: int) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days
