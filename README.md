# Stock Research Assistant

A personal research assistant for the Indian stock market. It researches continuously, and every trading morning
emails up to five stocks that have a **verified** reason to move up over the next few days to three weeks. Each one
gets a 1-5 rating, a hype-or-substance label, a holding window, and a link to every source behind it. It also
reviews the shares you already own and sends exit alerts.

It is a research tool, not a predictor and not investment advice. It never places orders.

The design is in [docs/PLAN.md](docs/PLAN.md). Server setup is in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## How it decides

1. **Safety gates** drop stocks you could not trade safely: low liquidity, exchange surveillance lists (ASM/GSM),
   trade-for-trade series, locked at the upper limit, promoter pledge above 25%.
2. **Six scores** (1-5 each): business quality, price and volume (including delivery %), trigger, order book,
   ownership, sentiment. Missing data is shown as "not found" and left out, never guessed.
3. **Caps**: no verified trigger, a jump of over 15% in five days, or under 60% data completeness each cap the
   rating at 3 stars. A verified red flag (auditor exit, insolvency, promoter selling, pledge rise) excludes the stock.
4. **Label**: hype-driven (3-5 days), mixed (1-2 weeks) or business-backed (2-3 weeks).

### Trust model

| Tier | Sources | Can establish a fact? |
|---|---|---|
| Official | NSE / BSE filings, shareholding, pledges, insider trades, bulk and block deals | Yes, on its own |
| Press | Economic Times, Mint, Business Standard, Moneycontrol, CNBC-TV18, BusinessLine, Financial Express, Reuters, PTI, NDTV Profit | Only when two independent outlets agree |
| Opinion | Broker calls and target prices (any headline with a recommendation) | No: sentiment only, lightly weighted |
| Blocked | Everything else: social media, Telegram, YouTube, forums, blogs | Never fetched into a report or shown to the AI |

The AI (Gemini) only reads documents the app fetched from approved sources. Every claim it makes must quote one of
those documents word for word; the quote is checked against the saved copy, and any number in the claim must
appear in the quote. Anything that fails is discarded. The rating itself is computed by code.

## Quick start (your laptop)

Requires Python 3.12.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; on Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env          # Linux/macOS: cp .env.example .env, then fill it in
stockresearch init-db
stockresearch feasibility       # checks every data source from this machine
stockresearch bootstrap --years 1
stockresearch market-close
stockresearch research --max 5
stockresearch publish
uvicorn stockresearch.web.app:app_factory --factory --port 8000
```

For local use over plain http set `COOKIE_SECURE=false` in `.env`. Open http://localhost:8000.

## Commands

| Command | When it runs | What it does |
|---|---|---|
| `stockresearch collect` | Hourly | New exchange filings and approved-press headlines, stored with saved copies |
| `stockresearch research` | Every 15 min | Works through the research queue (on-demand requests first) |
| `stockresearch market-close` | 19:00 IST, Mon-Fri | Official prices and delivery %, size groups, deals, surveillance lists, screen (per size group), the app's pretend trades and the reason each moved, rejected stocks, exit alerts |
| `stockresearch publish` | 08:00 IST, Mon-Fri | Rates the shortlist, picks up to three in each size group (no repeats), records rejected stocks, reviews holdings, sends the morning email |
| `stockresearch practice-weekly` | Fri 19:30 IST | Sends "How the app's picks did": results, why each moved, and the stocks it said no to |
| `stockresearch open-check` | 09:20 IST, Mon-Fri | Flags picks that opened more than 3% higher as stale |
| `stockresearch backtest --years 3` | By hand | Replays the price screen on history and compares it with the market |
| `stockresearch research-now SYMBOL` | By hand | Researches one stock immediately and prints its rating |
| `stockresearch import-holdings FILE` | By hand | Imports a broker holdings CSV |
| `stockresearch feasibility` | By hand | Checks every data source and notifier |
| `stockresearch idle-check` | Every 10 min (by the server's own timer) | Says whether the server may switch itself off; exit 0 = yes (plan v4) |
| `stockresearch backup --out DIR` | Nightly | Consistent copy of the database |

## Project layout

```
src/stockresearch/
  domain/        pure rules: trust tiers, verification, gates, scoring, rating, costs, exits, portfolio
  ports.py       interfaces to the outside world
  adapters/      NSE, Yahoo Finance, approved news feeds, Gemini, email, Telegram
  services/      jobs: market, collect, research, reports, publish, tracker, holdings, notify, backtest
  db/            SQLAlchemy models; migrations/ holds Alembic migrations
  web/           FastAPI pages and templates
  cli.py         the commands above
tests/           unit tests (domain), adapter parsing, end-to-end pipeline with fakes, web
deploy/          Caddy, systemd timers, backup script
```

## Development

```bash
ruff check src tests
mypy
pytest --cov=stockresearch
```

CI runs the same on every push (`.github/workflows/ci.yml`).

## Known limitations

- **It may not make money.** Public news is often in the price by the next morning. Paper trade for 4-8 weeks and
  read the track record before using real money.
- **Financial statements come from Yahoo Finance**, which derives them from company filings. NSE publishes them only
  as complex XBRL files, which v1 does not parse.
- **Mutual fund and foreign investor breakdown** is not read yet (it is inside XBRL shareholding files). The app uses
  the institutional total plus fund purchases and sales from bulk and block deals.
- **Google News links** in evidence go through a news.google.com redirect to the publisher. The publisher itself is
  checked against the allowlist before anything is stored.
- **NSE may block some cloud servers.** If it does, prices fall back to Yahoo and the run history says so; filings
  and ownership data would then be missing. Run `stockresearch feasibility` on the server first.
- Ownership data is quarterly: up to three months old. Dates are shown beside every figure.
- **Prices are not adjusted for splits and bonus issues** (NSE's files are unadjusted). A stock that split recently
  can show a false fall in its trend measures for a while.

## Differences from the plan

- Pages use plain HTML forms; HTMX turned out to be unnecessary.
- NSE's official price-limit list is used for the "locked at the upper limit" gate (not in the plan). Without it,
  large stocks with no fixed limit were wrongly flagged when they rose about 5%.
- On market holidays NSE serves the previous day's file under the holiday's name; the app checks the date inside
  each file so holidays don't create duplicate trading days.

## Legal

For personal use. Sharing or selling the picks to others requires SEBI registration as a Research Analyst or
Investment Adviser.

## Plan v3 (13 Nov 2026): what changed

See [docs/PLAN-v3-track-record-and-email.md](docs/PLAN-v3-track-record-and-email.md). In short:

- **Picks by size:** up to 3 each for large (Nifty 100), mid-size (Nifty Midcap 150) and small companies. A stock is a new
  pick once; while its pretend trade is running it is listed as "still running".
- **App's track record** replaces the old Track record and Practice pages: every pick is bought with Rs 10,000 of pretend
  money, with the reason it moved (company news, the whole market, its sector, faded excitement, or no clear reason
  found), plus a tab for stocks the app said no to.
- **My practice portfolio** is now the user's own pretend trades (`user_trades`), kept apart from the app's record.
- **Emails:** a table-first morning email and a weekly results email (Friday 19:30).
- Pretend trades made under rules version 1 (Rs 40,000 a day split between that day's picks) keep that rule.
