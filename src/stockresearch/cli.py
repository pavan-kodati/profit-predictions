"""Command-line entry points. The server's timers call these; you can also run them by hand.

    stockresearch init-db                  create or upgrade the database
    stockresearch feasibility              check every data source (milestone M0)
    stockresearch bootstrap [--years 3]    load the stock list and price history (first run)
    stockresearch market-close [--date D]  evening job: prices, deals, surveillance, screen, alerts
    stockresearch collect                  hourly job: new filings and approved headlines
    stockresearch research [--max N]       research queue worker (every 15 minutes)
    stockresearch publish                  morning job: rate, pick, email
    stockresearch open-check               after the open: flag stale picks
    stockresearch backtest [--years 3]     back-test the price screen
    stockresearch practice                 update the practice portfolio from stored prices
    stockresearch practice-weekly          send the Friday practice-portfolio email
    stockresearch research-now SYMBOL      research one stock immediately and print its rating
    stockresearch import-holdings FILE     import a broker holdings CSV
    stockresearch backup --out DIR         consistent copy of the database
    stockresearch idle-check               exit 0 if the server may switch itself off, else 1 (prints why)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from stockresearch import logging_setup


def _container():
    from stockresearch.container import build

    return build()


def init_db() -> None:
    from alembic import command
    from alembic.config import Config

    from stockresearch.config import get_settings

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, "head")


def cmd_feasibility() -> int:
    from stockresearch.services import feasibility

    checks = feasibility.run(_container())
    width = max(len(c.name) for c in checks)
    for ch in checks:
        mark = "OK  " if ch.ok else ("FAIL" if ch.required else "WARN")
        print(f"{mark}  {ch.name.ljust(width)}  {ch.detail}")
    failed = [c for c in checks if c.required and not c.ok]
    warned = [c for c in checks if not c.required and not c.ok]
    passed = len(checks) - len(failed) - len(warned)
    print(f"\n{passed} OK, {len(warned)} warning(s), {len(failed)} failure(s)"
          + (" - the app cannot run until the FAIL lines are fixed" if failed else ""))
    return 1 if failed else 0


def cmd_backup(out: Path) -> int:
    from stockresearch.config import get_settings
    from stockresearch.services.repo import now_ist

    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        print("backup command supports SQLite only; use pg_dump for PostgreSQL", file=sys.stderr)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"app-{now_ist():%Y%m%d-%H%M}.db"
    src = sqlite3.connect(url.removeprefix("sqlite:///"))
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    print(target)
    return 0


def cmd_idle_check() -> int:
    from stockresearch.config import get_settings
    from stockresearch.db.session import get_sessionmaker
    from stockresearch.services import idle

    decision = idle.check(get_settings(), get_sessionmaker())
    print(decision.reason)
    return 0 if decision.ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stockresearch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plain-logs", action="store_true", help="human-readable logs instead of JSON")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("feasibility")
    b = sub.add_parser("bootstrap")
    b.add_argument("--years", type=int, default=1)
    mc = sub.add_parser("market-close")
    mc.add_argument("--date", type=date.fromisoformat, default=None)
    sub.add_parser("collect")
    r = sub.add_parser("research")
    r.add_argument("--max", type=int, default=None)
    sub.add_parser("publish")
    sub.add_parser("open-check")
    bt = sub.add_parser("backtest")
    bt.add_argument("--years", type=int, default=3)
    sub.add_parser("practice")
    sub.add_parser("practice-weekly")
    rn = sub.add_parser("research-now")
    rn.add_argument("symbol")
    ih = sub.add_parser("import-holdings")
    ih.add_argument("file", type=Path)
    ih.add_argument("--horizon", choices=["short", "long"], default="long")
    sub.add_parser("idle-check")
    bk = sub.add_parser("backup")
    bk.add_argument("--out", type=Path, default=Path("data/backups"))
    args = p.parse_args(argv)
    code = _dispatch(args)
    # run_job returns 2 when it skipped because the same job is still running: that is normal, not a failure.
    return 0 if code == 2 and args.cmd in JOB_COMMANDS else code


JOB_COMMANDS = {"bootstrap", "market-close", "collect", "research", "publish", "open-check", "backtest", "practice",
                "practice-weekly"}


def _dispatch(args: argparse.Namespace) -> int:
    logging_setup.configure(json_output=not args.plain_logs)
    if args.cmd == "init-db":
        init_db()
        print("Database is up to date")
        return 0
    if args.cmd == "feasibility":
        return cmd_feasibility()
    if args.cmd == "backup":
        return cmd_backup(args.out)
    if args.cmd == "idle-check":
        return cmd_idle_check()

    from stockresearch.services import backtest, collect, market, publish, reports, research, tracker
    from stockresearch.services.runs import run_job

    c = _container()
    if args.cmd == "bootstrap":
        def _bootstrap(ctx):
            with ctx.stage("Refresh NSE stock list") as d:
                market.refresh_universe(ctx, d)
            return market.backfill_history(ctx, args.years)
        return run_job(c, "bootstrap", _bootstrap)
    if args.cmd == "market-close":
        return run_job(c, "market_close", lambda ctx: market.market_close(ctx, args.date))
    if args.cmd == "collect":
        return run_job(c, "collect", collect.collect)
    if args.cmd == "research":
        return run_job(c, "research", lambda ctx: research.process_queue(ctx, args.max))
    if args.cmd == "publish":
        return run_job(c, "publish", publish.publish)
    if args.cmd == "open-check":
        return run_job(c, "open_check", lambda ctx: ctx.soft_stage("Opening prices", lambda d: tracker.open_check(c, d)) and None)
    if args.cmd == "practice":
        from stockresearch.services import practice

        return run_job(c, "practice", lambda ctx: ctx.soft_stage("Practice portfolio", lambda d: practice.sync(c, d)) and None)
    if args.cmd == "practice-weekly":
        from stockresearch.services import notify

        return run_job(c, "practice_weekly", lambda ctx: f"sent via {notify.weekly_results(c)}")
    if args.cmd == "backtest":
        return run_job(c, "backtest", lambda ctx: backtest.backtest_job(ctx, args.years))
    if args.cmd == "research-now":
        sym = args.symbol.upper()
        research.research_symbol(c, sym)
        rid = reports.build_and_save(c, sym, purpose="on_demand")
        if rid is None:
            print(f"Not enough price history for {sym}")
            return 1
        from stockresearch.db import models as m
        from stockresearch.db.session import session_scope

        with session_scope(c.sessions) as s:
            rep = s.get(m.Report, rid)
            assert rep is not None
            print(f"{sym}: {rep.rating:.2f}/5 ({rep.stars} stars), {rep.pick_type}, completeness {rep.completeness:.0%}")
            for a in rep.data["areas"].values():
                print(f"  {a['label']:<17} {a['score'] if a['score'] is not None else 'n/a'}")
            for cap in rep.data["caps"]:
                print(f"  cap: {cap}")
            if rep.excluded_reason:
                print(f"  excluded: {rep.excluded_reason}")
            print(f"  {len(rep.evidence)} evidence line(s); report id {rid}")
        return 0
    if args.cmd == "import-holdings":
        from stockresearch.services import holdings

        result = holdings.import_csv(c, args.file.read_text(encoding="utf-8-sig"), args.horizon)
        print(f"Added {result.added} holding(s)")
        for line in result.skipped:
            print(f"  skipped: {line}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
