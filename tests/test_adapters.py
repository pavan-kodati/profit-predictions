"""Adapter parsing on recorded response shapes (taken from real NSE responses, trimmed)."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from stockresearch.adapters import gemini, nse
from stockresearch.adapters.news import short_name

BHAV = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
20MICRONS, EQ, 11-Nov-2026, 187.33, 187.5, 189.78, 186.2, 187.15, 188.12, 188.40, 52116, 98.19, 2089, 29010, 55.66
21STCENMGM, BE, 11-Nov-2026, 54.53, 57.2, 57.25, 56.1, 57.00, 56.58, 57.06, 25557, 14.58, 151, -, -
SOMEBOND, N1, 11-Nov-2026, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 5, 0.05, 1, -, -
"""


class FakeResp:
    def __init__(self, payload: Any = None, text: str = "", status: int = 200) -> None:
        self._payload, self.text, self.status_code = payload, text, status
        self.ok = status < 400

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def exchange(monkeypatch: pytest.MonkeyPatch) -> nse.NseExchange:
    ex = nse.NseExchange()
    ex._primed = True
    return ex


def test_bhavcopy_parsing(exchange: nse.NseExchange, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_csv(url: str) -> pd.DataFrame:
        df = pd.read_csv(io.StringIO(BHAV))
        df.columns = [c.strip() for c in df.columns]
        return df

    monkeypatch.setattr(exchange, "_csv", fake_csv)
    df = exchange.bhavcopy(date(2026, 11, 11))
    assert df is not None
    assert list(df["symbol"]) == ["20MICRONS", "21STCENMGM"]  # debt series dropped
    row = df.iloc[0]
    assert row["close"] == 188.12 and row["delivery_pct"] == 55.66 and row["series"] == "EQ"
    assert pd.isna(df.iloc[1]["delivery_pct"])


def test_holiday_file_with_other_session_is_ignored(exchange: nse.NseExchange, monkeypatch: pytest.MonkeyPatch) -> None:
    # NSE served 9 Nov's data under the 10 Nov (holiday) file name in real life.
    def fake_csv(url: str) -> pd.DataFrame:
        df = pd.read_csv(io.StringIO(BHAV))
        df.columns = [c.strip() for c in df.columns]
        return df

    monkeypatch.setattr(exchange, "_csv", fake_csv)
    assert exchange.bhavcopy(date(2026, 11, 10)) is None


def test_price_bands_parsing(exchange: nse.NseExchange, monkeypatch: pytest.MonkeyPatch) -> None:
    csv_text = ("Symbol,Series,Security Name,Band,Remarks\nM&M,EQ,MAHINDRA,No Band,-\nPDSL,EQ,PDS,20,-\n"
                "ABAN,BE,ABAN,2,-\nXBOND,N1,BOND,5,-\n")
    monkeypatch.setattr(exchange, "_csv", lambda url: pd.read_csv(io.StringIO(csv_text)))
    assert exchange.price_bands() == {"M&M": 0.0, "PDSL": 20.0, "ABAN": 2.0}


def test_announcements_parsing(exchange: nse.NseExchange, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"an_dt": "11-Nov-2026 18:29:54", "attchmntFile": "https://nsearchives.nseindia.com/corporate/AZAD.pdf",
                "attchmntText": "Received order", "desc": "Bagging/Receiving of orders/contracts", "symbol": "AZAD",
                "sm_name": "Azad Engineering Limited"},
               {"an_dt": None, "attchmntFile": "", "symbol": "X", "desc": "y"}]
    monkeypatch.setattr(exchange, "_api", lambda path, **kw: payload)
    rows = exchange.announcements(date(2026, 11, 11), date(2026, 11, 11))
    assert len(rows) == 1 and rows[0].symbol == "AZAD" and rows[0].published_at.hour == 18


def test_ownership_parsing(exchange: nse.NseExchange, monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "corporate-share-holdings-master": [
            {"date": "30-JUN-2026", "pr_and_prgrp": "55.42", "public_val": "44.58", "xbrl": "u1"},
            {"date": "30-SEP-2026", "pr_and_prgrp": "55.84", "public_val": "44.16", "xbrl": "u2"}],
        "corporate-pledgedata": {"data": [{"shp": "30-Sep-2026", "percSharesPledged": "4.53"}]},
        "corporates-pit": {"data": [
            {"secType": "Equity Shares", "tdpTransactionType": "Sell", "acqfromDt": "06-Jun-2025", "secVal": "5141068898",
             "acqName": "Rakesh Chopdar", "personCategory": "Promoters", "xbrl": "x"},
            {"secType": "Warrants", "tdpTransactionType": "Buy", "acqfromDt": "06-Jun-2025", "secVal": "1"}]},
    }
    monkeypatch.setattr(exchange, "_api", lambda path, **kw: responses[path])
    shp = exchange.shareholding("AZAD")
    assert shp[0].period_end == date(2026, 9, 30) and shp[0].promoter_pct == 55.84
    pledge = exchange.pledge("AZAD")
    assert pledge.pledged_pct == 4.53
    trades = exchange.insider_trades("AZAD")
    assert len(trades) == 1 and trades[0].side == "sell" and trades[0].value_rs == 5141068898


def test_surveillance_parsing(exchange: nse.NseExchange, monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "reportASM": {"longterm": {"data": [{"symbol": "A2ZINFRA", "survDesc": "LTASM Stage I"}]},
                      "shortterm": {"data": [{"symbol": "AGIIL", "survDesc": "STASM Stage I"}]}},
        "reportGSM": [{"symbol": "AGSTRA", "survDesc": "GSM stage 0"}],
    }
    monkeypatch.setattr(exchange, "_api", lambda path, **kw: responses[path])
    assert set(exchange.surveillance()) == {"A2ZINFRA", "AGIIL", "AGSTRA"}


def test_gemini_reply_parsing() -> None:
    reply = '```json\n{"thesis": "x", "events": [{"type": "order_win", "polarity": 1, "title": "t", "quote_doc_id": 3, ' \
            '"quote": "q"}], "pick_type": "mixed", "risks": ["a", " ", "b"]}\n```'
    findings = gemini.parse_findings(reply)
    assert findings.events[0].quote_doc_id == 3 and findings.pick_type == "mixed" and findings.risks == ["a", "b"]
    with pytest.raises(ValueError):
        gemini.parse_findings("no json here")
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        gemini.parse_findings('{"events": [{"polarity": 5}]}')


def test_render_documents_respects_budget() -> None:
    from stockresearch.ports import ResearchDocument

    docs = [ResearchDocument(i, "NSE", 1, "2026-11-11", f"t{i}", "x" * 10_000) for i in range(40)]
    rendered = gemini.render_documents(docs)
    assert len(rendered) <= gemini.MAX_TOTAL_CHARS + 500
    assert "[doc 0]" in rendered


def test_short_name() -> None:
    assert short_name("Azad Engineering Limited") == "Azad Engineering"
    assert short_name("Bharat Electronics Ltd.") == "Bharat Electronics"


def test_alembic_migration_matches_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    from stockresearch.db.models import Base

    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(nse.__file__).resolve().parent.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
