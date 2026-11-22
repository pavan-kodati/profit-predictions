"""Back-test, track-record outcomes, feasibility check and the CLI."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from sqlalchemy import select

from stockresearch.container import Container
from stockresearch.db import models as m
from stockresearch.db.session import session_scope
from stockresearch.services import backtest, feasibility, repo, tracker
from stockresearch.services.runs import run_job
from tests import fakes
from tests.test_pipeline import bootstrap, full_day


def test_backtest_runs_on_stored_history(container: Container) -> None:
    bootstrap(container)
    result = backtest.run(container, years=2, top_n=2)
    assert "error" not in result, result
    assert result["trades"] > 0 and 0 <= result["picks"]["hit_rate"] <= 1
    assert run_job(container, "backtest", lambda ctx: backtest.backtest_job(ctx, 2)) == 0
    with session_scope(container.sessions) as s:
        assert s.scalar(select(m.BacktestResult)) is not None


def test_backtest_without_history_reports_error(container: Container) -> None:
    assert "error" in backtest.run(container, years=1)


def test_outcomes_measured_from_next_open_after_costs(container: Container) -> None:
    full_day(container)
    with session_scope(container.sessions) as s:
        pick = s.scalar(select(m.Pick))
        assert pick is not None
        ref = pick.ref_close
        rows = []
        for i, day in enumerate(pd.bdate_range(fakes.TODAY + timedelta(days=1), periods=12)):
            price = ref * (1 + 0.01 * (i + 1))
            rows.append({"symbol": pick.symbol, "date": day.date(), "open": price, "high": price * 1.01,
                         "low": price * 0.99, "close": price, "volume": 1e6, "delivery_pct": 50.0, "series": "EQ",
                         "source": "nse"})
        repo.store_prices(s, rows)
    detail: list[str] = []
    tracker.update_outcomes(container, detail)
    with session_scope(container.sessions) as s:
        pick = s.scalar(select(m.Pick))
        assert pick is not None and pick.outcome is not None
        o = pick.outcome
        assert o.entry_price == round(ref * 1.01, 2)
        assert o.ret_10 is not None and o.ret_10 > 0.09
        assert o.net_ret_10 is not None and o.net_ret_10 < o.ret_10  # charges and tax deducted
        picks = list(s.scalars(select(m.Pick)))
        summary = tracker.summary(picks)
        assert summary["All picks"]["count"] == 1 and summary["All picks"]["hit_rate"] == 1.0


def test_feasibility_with_fakes(container: Container) -> None:
    checks = feasibility.run(container)
    names = {c.name: c for c in checks}
    assert names["NSE stock list"].ok
    assert names["NSE end-of-day file with delivery %"].ok
    assert names["Gemini API"].ok
    assert all(c.ok for c in checks if c.required), [c for c in checks if not c.ok]


def test_cli_init_db_and_backup(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from stockresearch import cli, config
    from stockresearch.db import session as db_session

    db = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    config.get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    try:
        assert cli.main(["--plain-logs", "init-db"]) == 0
        assert db.exists()
        assert cli.main(["--plain-logs", "backup", "--out", str(tmp_path / "bk")]) == 0
        assert list((tmp_path / "bk").glob("app-*.db"))
    finally:
        config.get_settings.cache_clear()
        db_session.get_engine.cache_clear()
        db_session.get_sessionmaker.cache_clear()


def test_unfinished_trading_day_not_stored(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime

    idx = pd.to_datetime(["2026-11-11", "2026-11-12"])
    df = pd.DataFrame({"open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0], "volume": [5, 5]},
                      index=idx)
    monkeypatch.setattr(repo, "now_ist", lambda: datetime(2026, 11, 12, 13, 38))  # market open
    assert [r["date"].isoformat() for r in repo.frame_to_rows("X", df, "yahoo")] == ["2026-11-11"]
    monkeypatch.setattr(repo, "now_ist", lambda: datetime(2026, 11, 12, 19, 0))  # after the close
    assert len(repo.frame_to_rows("X", df, "yahoo")) == 2


def test_package_data_includes_every_template_and_asset() -> None:
    """Files missing from package-data work in tests but vanish in the Docker image (happened with daily.txt)."""
    import fnmatch
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    patterns = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["stockresearch"]
    pkg = root / "src" / "stockresearch"
    needed = [p for p in pkg.rglob("*") if p.is_file() and p.suffix not in {".py", ".pyc"} and "__pycache__" not in p.parts]
    missing = [str(p.relative_to(pkg)) for p in needed
               if not any(fnmatch.fnmatch(p.relative_to(pkg).as_posix(), pat) for pat in patterns)]
    assert not missing, f"not packaged: {missing}"
