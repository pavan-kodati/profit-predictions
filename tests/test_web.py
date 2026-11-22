from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.web.app import create_app
from tests.test_pipeline import full_day


@pytest.fixture()
def client(container: Container) -> TestClient:
    return TestClient(create_app(container), base_url="http://testserver")


def login(client: TestClient) -> None:
    r = client.post("/login", data={"username": "pavan", "password": "correct-horse-battery", "next": "/"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_pages_require_login(client: TestClient) -> None:
    for path in ("/", "/holdings", "/track-record", "/runs", "/backtest", "/reports/1", "/documents/1"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_health_is_public(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_wrong_password_and_lockout(client: TestClient) -> None:
    for _ in range(5):
        r = client.post("/login", data={"username": "pavan", "password": "nope"})
        assert "Wrong username or password" in r.text
    r = client.post("/login", data={"username": "pavan", "password": "correct-horse-battery"})
    assert "Too many failed attempts" in r.text


def test_open_redirect_blocked(client: TestClient) -> None:
    r = client.post("/login", data={"username": "pavan", "password": "correct-horse-battery", "next": "//evil.example"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"


def test_cross_site_post_refused(client: TestClient) -> None:
    login(client)
    r = client.post("/research", data={"symbol": "GOODCO"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_security_headers(client: TestClient) -> None:
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    # Must not be no-referrer: that makes browsers send "Origin: null" on form posts and breaks login.
    assert r.headers["referrer-policy"] == "same-origin"


def test_same_origin_post_with_browser_origin_header(client: TestClient) -> None:
    r = client.post("/login", data={"username": "pavan", "password": "correct-horse-battery"},
                    headers={"origin": "http://testserver"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.post("/login", data={"username": "pavan", "password": "correct-horse-battery"},
                    headers={"origin": "null"}, follow_redirects=False)
    assert r.status_code == 403


def test_all_pages_render_with_data(container: Container, client: TestClient) -> None:
    full_day(container)
    login(client)
    with session_scope(container.sessions) as s:
        report_id = s.scalar(select(m.Pick.report_id))
        doc_id = s.scalar(select(m.SourceDocument.id))
    r = client.get("/")
    assert r.status_code == 200 and "Good Company" in r.text
    r = client.get(f"/reports/{report_id}")
    assert r.status_code == 200
    assert "Sources that support it" in r.text and "Official NSE filing" in r.text and "Saved copy" in r.text
    assert "In plain words" in r.text and "Why it might go up" in r.text and "If you put" in r.text
    assert "Practise buying this" in r.text and "Sell if it falls to" in r.text
    assert client.get(f"/documents/{doc_id}").status_code == 200
    assert client.get("/stocks/GOODCO").status_code == 200
    assert client.get("/track-record").status_code == 200
    assert client.get("/track-record?tab=rejected").status_code == 200
    assert client.get("/track-record?group=small").status_code == 200
    assert client.get("/practice").status_code == 200
    assert client.get("/runs").status_code == 200
    assert client.get("/backtest").status_code == 200


def test_holdings_flow(container: Container, client: TestClient) -> None:
    full_day(container)
    login(client)
    r = client.post("/holdings", data={"symbol": "GOODCO", "quantity": "10", "avg_price": "120", "horizon": "short",
                                       "buy_date": "2026-10-01", "stop_price": "100"})
    assert r.status_code == 200 and "Holding added" in r.text
    r = client.post("/holdings", data={"symbol": "NOTREAL", "quantity": "1", "avg_price": "1", "horizon": "long"})
    assert "Unknown NSE symbol" in r.text
    files = {"file": ("h.csv", b"Symbol,Quantity,Average Price\nWEAKCO,4,99\n", "text/csv")}
    r = client.post("/holdings/import", files=files, data={"horizon": "long"})
    assert "Imported 1 holding" in r.text
    with session_scope(container.sessions) as s:
        hid = s.scalar(select(m.Holding.id).where(m.Holding.symbol == "GOODCO"))
    assert client.get(f"/holdings/{hid}").status_code == 200
    r = client.post(f"/holdings/{hid}/close")
    assert "marked as sold" in r.text


def test_on_demand_research_request(container: Container, client: TestClient) -> None:
    full_day(container)
    login(client)
    r = client.post("/research", data={"symbol": "weakco"}, follow_redirects=False)
    assert r.headers["location"] == "/stocks/WEAKCO?queued=1"
    with session_scope(container.sessions) as s:
        item = s.scalar(select(m.ResearchQueueItem).where(m.ResearchQueueItem.symbol == "WEAKCO",
                                                          m.ResearchQueueItem.on_demand.is_(True)))
        assert item is not None and item.priority == 0
    r = client.post("/research", data={"symbol": "<script>"}, follow_redirects=False)
    assert "error=unknown" in r.headers["location"]


def test_templates_have_no_inline_styles_or_scripts() -> None:
    """The CSP blocks inline style attributes and inline scripts, so they would silently not work."""
    import re
    from pathlib import Path

    templates = Path(__file__).resolve().parent.parent / "src" / "stockresearch" / "web" / "templates"
    for path in templates.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\sstyle=", text), f"inline style in {path.name}"
        assert not re.search(r"\son[a-z]+=", text), f"inline event handler in {path.name}"
        assert not re.search(r"<script(?![^>]*\ssrc=)", text), f"inline script in {path.name}"


def test_login_page_and_signed_in_redirect(client: TestClient) -> None:
    r = client.get("/login")
    assert r.status_code == 200 and "Sign in to your account" in r.text and 'data-toggle-password="p"' in r.text
    login(client)
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_sparkline_points() -> None:
    from stockresearch.web.views import sparkline

    s = sparkline([10, 11, 12, 11.5, 13])
    assert s is not None and s["dir"] == "up" and s["line"].startswith("0.0,40.0")
    assert sparkline([5]) is None
