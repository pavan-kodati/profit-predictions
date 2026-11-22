# Stock Research Assistant: the complete guide

**Read this first if you are new to the project** (a new teammate, or a fresh Claude session). It explains the whole
application in plain words: what it does, how it decides, what runs when, how it is hosted, what is finished, and what
is still open. The other files in `docs/` are the detailed history; this one is the map.

| | |
|---|---|
| **Owner** | Pavan (personal project, single user) |
| **Written** | 22 Nov 2026, from reading the code and the other docs in this folder |
| **Project folder** | `profit-predictions` (Python package name: `stockresearch`) |
| **Not verified when this was written** | The live server (it was not contacted), whether the tests currently pass (not run), and the AWS-side objects listed in section 12. Section 14 says how to check each. |

## Contents

1. [The application in one minute](#1-the-application-in-one-minute)
2. [A day in the life: what runs when](#2-a-day-in-the-life-what-runs-when)
3. [How a stock becomes a pick](#3-how-a-stock-becomes-a-pick)
4. [Pretend money: the app's track record](#4-pretend-money-the-apps-track-record)
5. [Your own shares: holdings, alerts and practice trades](#5-your-own-shares-holdings-alerts-and-practice-trades)
6. [The website](#6-the-website)
7. [The emails](#7-the-emails)
8. [The code: a map for developers](#8-the-code-a-map-for-developers)
9. [The database](#9-the-database)
10. [Settings (environment variables)](#10-settings-environment-variables)
11. [Commands](#11-commands)
12. [Hosting on AWS, the weekday schedule, backups and cost](#12-hosting-on-aws-the-weekday-schedule-backups-and-cost)
13. [Running it on a laptop, tests and quality checks](#13-running-it-on-a-laptop-tests-and-quality-checks)
14. [Status: what is done, what is live, what is open](#14-status-what-is-done-what-is-live-what-is-open)
15. [Troubleshooting](#15-troubleshooting)
16. [Rules for anyone working on this (including Claude)](#16-rules-for-anyone-working-on-this-including-claude)
17. [History and where the other documents fit](#17-history-and-where-the-other-documents-fit)
18. [Plain-words glossary](#18-plain-words-glossary)

---

## 1. The application in one minute

**What it is.** A private web application and email service for the **Indian stock market (NSE)**. It reads official
exchange filings and trusted newspapers all day. On every trading morning at 08:00 India time it emails up to **three
stock picks in each company-size group** (large, mid-size, small), each with a rating from 1 to 5, a plain-language
reason, the risks, a safety price to sell at, and links to every source behind it. It also keeps score of its own
picks using pretend money, and it watches the shares you really own.

**What it is not.** It is not a predictor, not investment advice, and it **never places orders**. Its own README says
it may not make money: public news is often already in the price by the next morning. The plan is to watch its pretend
results for 4 to 8 weeks before trusting it with real money.

**Who it is for.** One person (Pavan), who buys shares (no futures or options) and holds them from a few days to about
three weeks. It is written so that a beginner can understand each pick in a minute, while an expert can open the
details and check every claim.

**The five ideas that make it trustworthy**

1. **Only approved sources.** Official exchange records can establish a fact alone. Established newspapers count only
   when two different ones agree. Broker "buy" calls are opinion, lightly weighted. Everything else (social media,
   Telegram, YouTube, forums, blogs) is never read.
2. **The AI is a reader, not a source.** Google's Gemini model only reads documents the app already saved. Every claim it
   makes must quote a saved document word for word, and any number in the claim must appear in that quote. Claims that
   fail are thrown away.
3. **The rating is computed by code**, from six scores and fixed caps. The AI cannot change the number.
4. **"Not found" is shown, never guessed.** Missing data is left out of the maths and listed on the report.
5. **Honest scorekeeping.** Every pick is "bought" with pretend money using prices that existed at the time, with real
   costs and tax. Trades are never edited, and rules are versioned.

---

## 2. A day in the life: what runs when

All times are **India Standard Time (IST)**. "Mon-Fri" means NSE trading weekdays (the app also copes with market
holidays: it checks the date inside each NSE file).

| Time | Job (command) | What happens |
|---|---|---|
| Every hour at :05 | `collect` | Downloads new NSE filings and headlines from approved newspapers. Saves a copy of each. Adds stocks to the research queue when something important appears. |
| Every 15 minutes | `research` | Takes up to 4 stocks from the research queue (holdings first, then positive filings, then candidates) and researches each one. |
| 19:00 Mon-Fri | `market-close` | The big evening job: official prices and delivery %, size groups, bulk/block deals, exchange watch lists, the screen that builds tomorrow's shortlist, pretend trades, "stocks we said no to", exit alerts for your holdings. |
| 08:00 Mon-Fri | `publish` | Rates the shortlist, picks up to 3 per size group, reviews your holdings, **sends the morning email**. |
| 09:20 Mon-Fri | `open-check` | If a pick opened more than 3% above its reference price, flags it as "stale" and sends an alert. |
| 19:30 Friday | `practice-weekly` | Sends the **weekly results email** ("How the app's picks did"). |
| Before every self-shutdown, and nightly 02:30 | backup | Copies the database, keeps 30 days locally and syncs to S3. |
| Every 10 minutes | `idle-check` (by the server's own timer) | Decides whether the server may switch itself off (section 12). |

**The server itself only runs on weekdays**, from 07:30 until the evening work is done (about 21:00), then it switches
itself off to save money. Details in section 12.

**What arrives in your inbox**

| Email | When | Contents |
|---|---|---|
| Morning email | 08:00 Mon-Fri | Subject like "3 new picks for Fri 13 Nov: ABC, DEF, GHI" or "No new picks for ...". A summary table, one card per pick, earlier picks still running. |
| Weekly results | Fri 19:30 | One sentence in rupees, a table of every pick's result, why each moved, and the stocks the app said no to. |
| Alerts | When something needs attention | Exit alerts on shares you hold, stale picks. |
| Failure alert | When a job fails | At most one per job every 6 hours. |

---

## 3. How a stock becomes a pick

This is the heart of the application. Each step has its own code module (section 8).

### Step 1. The universe and the size groups

- The app tracks about **750 stocks**: NSE's "Nifty Total Market" list, refreshed when it is over 7 days old.
- Each stock is put in a **size group** using NSE's official index lists:

| Group | NSE list | Plain description |
|---|---|---|
| Large | Nifty 100 | The 100 biggest companies; steadier prices |
| Mid-size | Nifty Midcap 150 | Ranked 101 to 250; faster growth, bigger swings |
| Small | everything else it scans | Ranked 251 and below; riskiest |

If the lists cannot be downloaded, market value is used as a fallback (about Rs 1,00,000 crore and above = large,
Rs 33,000 crore and above = mid-size).

### Step 2. Collecting facts (`collect`, hourly)

- **Filings** from NSE announcements. A table of categories decides what each filing is:
  - *Clear-cut and positive*: orders won, capacity added, product launches, acquisitions, buybacks, open offers.
  - *Clear-cut and negative (red flags)*: auditor resignation, insolvency, default, fraud, one-time settlements, late results.
  - *Needs reading by the AI*: board-meeting outcomes, financial results, press releases, investor presentations, credit ratings, management changes.
  - *Routine paperwork* (trading window notices, record dates, certificates): stored, never scored.
- **Headlines** from the RSS feeds of Economic Times, Mint, Business Standard, Hindu BusinessLine and CNBC-TV18, plus a
  Google News search per company. Each headline's real publisher is checked against the allowlist before anything is saved.
  Whole-market headlines (Nifty, Sensex, market crash or rally) are kept separately; they are used later to explain moves.
- After a weekend off, `collect` looks back to its last successful run (at most 5 days), so weekend announcements are not missed. Filings are de-duplicated, so looking twice is harmless.

### Step 3. The trust model (who is allowed to say what)

| Tier | Sources | Can it establish a fact? |
|---|---|---|
| Official | NSE and BSE: filings, shareholding, pledges, insider trades, bulk and block deals | Yes, on its own |
| Press | Economic Times, Mint, Business Standard, Moneycontrol, CNBC-TV18, Hindu BusinessLine, Financial Express, Reuters, PTI, NDTV Profit | Only when **two different outlets** report it |
| Opinion | Any headline carrying a recommendation (target price, "buy call", "upgrade", "top picks" and similar) | No. Counts a little towards sentiment only |
| Blocked | Everything else, plus forum, blog, sponsored and reader-blog pages even on allowed sites | Never fetched into a report or shown to the AI |

An event is **verified** (official filing, or two different newspapers), **reported** (one newspaper: shown, labelled
"single source", counts at half weight) or **unverified** (not scored).

### Step 4. The evening screen (`market-close`, 19:00)

Narrows ~750 stocks to a research shortlist:

1. Drop stocks trading less than **Rs 5 crore a day** (hard to sell).
2. Keep stocks in an uptrend (above the 50-day average price) that show fresh activity: volume at least 1.5 times normal, up 5% or more in 5 days, or within 5% of the one-year high. A fresh positive filing also qualifies.
3. Rank by volume (30%), 5-day gain (20%), 20-day gain (15%), closeness to the high (15%), fresh filing (15%) and rising delivery % (5%).
4. Keep the **top 10 in each size group** (`SHORTLIST_PER_GROUP`) so large, mid and small companies each get a fair chance.

### Step 5. Research (`research`, every 15 minutes)

For one stock the app: loads price history; gets financial statements from Yahoo Finance; stores shareholding,
promoter pledges and insider trades from NSE; stores filings (last 30 days) and news (last 14 days); reads up to 4 PDFs;
looks up the next results date; applies a rule that turns a fresh results filing into a positive or negative event by
comparing profit with a year ago; then hands up to **30 saved documents** to Gemini.

Gemini returns a thesis, events, evidence for and against, an order-book figure, risks, a "future story" flag and a
suggested label. The app then **validates every finding**: the quote must exist in the saved copy of that document
(whitespace, case and quote marks ignored, at least 12 characters), numbers in the claim must appear in the quote, and
opinion documents may only inform sentiment. Anything that fails is dropped and logged.

AI limits: at most **200 calls a day** and 6 seconds between calls (`GEMINI_DAILY_CALL_BUDGET`, `GEMINI_MIN_INTERVAL_SECONDS`).
When the budget is used up or no key is set, the app still works on rules and official data only, and says so in the email.
You can also press a button to research any stock on demand (10 a day).

### Step 6. Safety gates (a stock that fails any is never a pick)

| Gate | Why |
|---|---|
| Average daily trading below Rs 5 crore | Hard to sell quickly |
| On NSE's surveillance lists (ASM/GSM) | Over-speculated; can lock at the lower limit so you cannot exit |
| Trading in restricted series BE, BZ, BT or IT | Trade-for-trade: no intraday exit |
| Locked at the upper price limit | You could not actually buy it. (Uses NSE's official price-limit list; stocks with no fixed limit are never flagged.) |
| Promoter shares pledged above 25% | Forced-selling risk |
| *Flag only, not a failure:* results due within 21 days | The reaction to results is unpredictable |

### Step 7. Six scores, each 1 to 5, and the rating

| Area | Weight | What it looks at |
|---|---|---|
| Business quality | 25% | Sales and profit growth (latest year and 3-year), quarterly profit trend, margin, debt, return on equity, how expensive (P/E over 80 is penalised) |
| Price and volume | 20% | Above 50- and 200-day averages, volume vs normal, **delivery %**, one-month gain, near one-year high; penalties for "overheated" and for "volume up but delivery down" |
| Trigger | 25% | Verified positive events in the last 10 days (weighted by type and, for orders, size versus yearly sales); negative events in the last 30 days subtract |
| Order book | 10% | Pending orders and new orders in 90 days versus yearly sales |
| Ownership | 10% | Promoter holding and its change, pledges, institutional holding, insider and fund buying or selling |
| Sentiment | 10% | Tone of approved press coverage; analyst views count lightly |

Areas with no data are left out and the rest are re-weighted. The report shows a **data completeness** percentage.

**Caps (rating cannot exceed 3.0 stars):** no verified trigger; up more than 15% in 5 days (late entry); under 60% data
completeness. **Exclusion:** a verified red-flag event in the last 30 days pushes the rating to 2.0 at most and the stock
is excluded; failing a safety gate also excludes it.

**The label (hype or substance):**

| Label shown | Meaning | Holding window used |
|---|---|---|
| Mostly excitement ("hype") | Price ran ahead of profits, speculative or overheated trading, weak business, or a future story | 5 trading days |
| Some real news, some excitement ("mixed") | Real trigger, but the price already reflects much of it | 10 trading days |
| Backed by business results ("fundamentals") | Verified trigger, strong business, price not stretched | 15 trading days |

The AI may only push the label *towards caution*, never towards "business-backed".

**The safety price ("sell if below")** is mechanical: `price x (1 - risk)` where risk is twice the stock's typical daily
range, kept between 4% and 12%. It is a starting point, not advice.

### Step 8. Choosing the picks (`publish`, 08:00)

1. Rate every shortlisted stock from stored data.
2. Keep stocks with no exclusion and a rating of **3.5 or more** (`PICK_MIN_RATING`).
3. Sort best first; take up to **3 per size group** (`PICKS_PER_GROUP`).
4. **No repeats:** a stock is a new pick once. While its pretend trade is open it appears as "still running". It can be picked again only after the trade closes **and** a new verified positive event has happened since.
5. A good stock that did not fit gets the note "stronger picks filled its size group today".
6. Everything else that was rated but not picked is recorded as a **rejection** to follow (section 4).
7. Review holdings, send the morning email, send pending alerts.

A day with **no picks is a valid answer** and the email says so.

---

## 4. Pretend money: the app's track record

The app holds itself to account. Every pick is automatically "bought" with **Rs 10,000** of pretend money.

**Rules (version 2, from 13 Nov 2026)**

- **Buy** whole shares at the **first opening price a reader could actually get**. A pick published at 08:00 buys at that morning's 09:15 open; one published after the open buys the next day.
- If one share costs more than Rs 10,000, buy 1 share and say so.
- **Skip** the pick if it opens at or below its safety price (the reason to buy has already failed).
- **Sell** at the first of: price touches the safety price (sold at that price); a day opens below the safety price (sold at that opening price, as would really happen); the holding window ends (sold at that day's close).
- **Costs and tax are always deducted:** brokerage Rs 20 per order, DP charge Rs 15.93 per sell, securities transaction tax 0.1% each side, exchange and SEBI fees, stamp duty 0.015% on the buy, 18% GST on fees; **20% short-term capital-gains tax** on profit.
- **No hindsight:** a trade only uses prices that existed on that day. Trades are **never edited**. Trades made under version 1 (Rs 40,000 a day split among that day's picks, until 13 Nov) keep that rule.

**The verdict** compares the app's pretend money with the Nifty 50 and a 7% fixed deposit over the same days. It says
"Too early to tell" until there are **20 finished trades and 28 days**, then "Beating the market so far" or "Not beating the market".

**"Why it moved"** is chosen by fixed rules in this order, so it cannot be made up:

| Shown as | Rule |
|---|---|
| The business | A confirmed company event happened while held, matching the direction of the move |
| The whole market | The Nifty moved 1.5% or more the same way and covers at least half of the stock's move (shows what trusted newspapers said on the biggest day) |
| Its sector | Same-industry shares moved 2% or more the same way, covering at least half |
| Excitement faded | A "mostly excitement" pick that fell with no news |
| Hardly moved | Under 1% change |
| No clear reason found | None of the above (said plainly) |

This shows what moved *with* the price: strong evidence, not proof.

**Stocks the app said no to.** Every stock that was rated but not picked is followed for **10 market days** against the
Nifty 50. Verdicts: *Right to skip* (it fell, or trailed the Nifty by more than 3 points), *Made little difference* (within 3 points), *Missed a gain* (beat the Nifty by more than 3 points).

**The back-test** (`/backtest`, `stockresearch backtest`) replays only the price-and-volume screen on past data (top 5 a day, bought next open, held 10 days) against buying every liquid stock. News and filings cannot be back-tested; that is what the pretend trading is for.

---

## 5. Your own shares: holdings, alerts and practice trades

**Holdings.** Add shares by hand or import a broker CSV (Zerodha, Groww, Upstox and similar; column names are guessed).
Each holding is "short" or "long" horizon. **Privacy:** quantities and prices stay in the database and are never sent to the AI.

**Daily review.** Each holding gets a fresh report and a health status:

- **Intact**: no negative evidence found, business and trend checks hold.
- **Weakening**: weak business score, rating under 3, or price below its 200-day (long) or 50-day (short) average.
- **Broken**: a verified negative event, two quarters of falling profit, promoters cutting their stake by 2 points or more, or pledges above 25%.

It also shows an "averaging down" check when you are below your buy price, tax-timing hints (short vs long term), and the filings since you bought. It gives **evidence and status only, never "keep", "sell" or "buy more".**

**Exit alerts** (market-close job, emailed): your stop price hit (exit), a verified negative event (exit), price below 10-day average, volume dried up, or +10% reached (short horizon); price below 200-day average (long horizon).

**My practice portfolio.** The **"Practise buying this"** button on any report creates *your own* pretend trade (amount of your choice, default Rs 10,000), bought at the next open, with a **Sell now** button. Same rules, costs and tax, but kept completely apart from the app's record, so your choices never change the app's score.

---

## 6. The website

A server-rendered site (FastAPI and Jinja2 templates, one stylesheet, a little JavaScript for tap-to-explain terms). Pages only read the database; they never call outside services.

| Menu item | Address | What it shows |
|---|---|---|
| Today's picks | `/` | New picks grouped by size, picks still running, and the other stocks rated that day (a `day` option looks at earlier days) |
| Report | `/reports/{id}` | One stock's rating, six scores with reasons, evidence for and against with quote and link, what could go wrong, price chart, a Practise buying this button |
| Saved document | `/documents/{id}` | The app's own saved copy of a source, so ratings can be audited months later |
| Stock | `/stocks/{symbol}` | The last 20 reports for a stock, plus a button to research it now (10 a day) |
| App's track record | `/track-record` | Pretend results, why each moved, filter by size group; second tab: stocks the app said no to |
| My practice portfolio | `/practice` | Your own pretend trades |
| My holdings | `/holdings`, `/holdings/{id}` | Real shares, health status, import CSV |
| How it works | `/how-it-works` | The rules in plain words |
| Back-test | `/backtest` | History replay of the price screen |
| System | `/runs` | Every job run with its stages, AI usage today |
| Health | `/health` | Used by Docker and for a quick "is it up" check |

**Security:** a single user; password of at least 12 characters and a session secret of at least 32 (otherwise login is
disabled); signed cookie valid 14 days, `SameSite=strict`, `Secure` over https; 5 wrong passwords in 15 minutes locks that
address out; form posts are checked for same origin; a strict content-security policy (no inline scripts or styles);
`X-Frame-Options: DENY`; HSTS when secure. Caddy provides the https certificate automatically.

**Idle tracking:** every signed-in page view writes the time to `data/.last_visit` (at most once a minute) so the server
does not switch itself off while you are using the site.

---

## 7. The emails

All email is built from Jinja2 templates in `src/stockresearch/notifications/templates/` and sent by `services/notify.py`
through Gmail's SMTP (an app password, never the normal password). Telegram is supported as an optional second channel
(plain text only).

| Template | Used for |
|---|---|
| `email_base.html` | Shared frame: 640px card, header, footer links (Open the app, App's track record, How it works) and the disclaimer; dark-mode and small-screen styles |
| `_email_ui.html` | Shared pieces: number tiles, section headings, the positions table, the button, colours |
| `daily.html` / `daily.txt` | Morning email, HTML and plain-text versions |
| `weekly.html` / `weekly.txt` | Friday results email |
| `alerts.html` | Holding and stale-pick alerts (simpler layout) |

**Morning email layout** (from plan v3, "layout B"): a headline sentence, any alerts on shares you hold, then a **summary
table of picks grouped by size** (stock, rating, shares for Rs 10,000, "sell if below" price and "lose up to" amount), the
earlier picks still running, and then **one card per pick** with rating, label, "Why it might go up", "What could go
wrong", four number tiles ("Buy", "You pay", "Sell if it falls to", "Most you'd lose"), up to two "Check it yourself" source
links and a "Read the full report" button. If a size group has no pick it says so.

**Weekly email layout:** four tiles (Put in, Worth now, Gain, In profit), comparison with the Nifty 50 and a fixed
deposit, tables of "Still holding" and "Finished", a "Why they moved" list, and "Stocks the app said no to".

**Readability work (22 Nov 2026).** The templates were redesigned for easier reading: table-first layout, plain labels, tiles
instead of long sentences, a hidden preview line shown in inbox lists, a phone layout (columns shrink, tiles go two by
two) and a dark mode. The plain-text versions were updated too. Tests that check these elements exist in
`tests/test_practice_flow.py` (`test_morning_email_layout`, `test_weekly_results_email`). The older `alerts.html` was not
part of that redesign.

Every email ends with: *Automated research, not investment advice and not a prediction. Prices can fall as well as rise.
Check the linked sources yourself before investing.*

---

## 8. The code: a map for developers

Python 3.12. Main libraries: FastAPI, SQLAlchemy 2 with Alembic migrations, SQLite, pandas, yfinance, feedparser, pypdf, google-genai, Jinja2.

```
profit-predictions/
  src/stockresearch/
    domain/        PURE RULES: no database, no network. The place where decisions live.
      sources.py       trust tiers and the allowlist
      verification.py  verified / reported / unverified; checks AI quotes and numbers
      filings.py       maps NSE filing categories to event types
      technicals.py    price and volume measures (averages, delivery %, upper-limit test)
      gates.py         safety gates
      scoring.py       the six area scores
      rating.py        weights, caps, hype/mixed/business label, the safety price
      screening.py     the evening shortlist
      capgroup.py      large / mid-size / small
      costs.py         brokerage, taxes and charges
      practice.py      pretend-trade rules (version 2), verdict
      attribution.py   "why it moved" and "was skipping right"
      exits.py         alerts for shares you hold
      portfolio.py     holding health status, averaging check, tax timing
      models.py        shared data types
    ports.py       interfaces to the outside world (exchange, market data, news, AI, notifier)
    adapters/      the real implementations of those interfaces
      nse.py           NSE website and archives (prices, filings, deals, lists)
      yahoo.py         Yahoo Finance (history, financial statements, today's open)
      news.py          approved RSS feeds and Google News search
      gemini.py        the AI (prompt, strict reply format, retries)
      notifiers.py     email (SMTP) and Telegram
      http.py          shared web client: polite rate limit, retries, timeouts
    services/      JOBS: orchestration that joins rules, adapters and the database
      collect.py market.py research.py reports.py publish.py practice.py reasons.py
      rejections.py tracker.py holdings.py notify.py backtest.py feasibility.py
      idle.py (server self-shutdown rules)  runs.py (job runner)  repo.py (database helpers)
    db/            SQLAlchemy models (models.py) and sessions
    migrations/    Alembic: 0001 initial, 0002 stock price band, 0003 practice trades, 0004 track record v3
    web/           FastAPI app, views, login, glossary, templates, static files
    notifications/templates/   email templates
    presenters.py  turns reports and trades into plain-language text for pages and emails
    config.py      settings    container.py  wires real adapters (tests use fakes)
    cli.py         every command in section 11
  tests/           unit tests per rule, adapter parsing, end-to-end pipeline with fakes, web pages, idle rules
  deploy/          Caddyfile, systemd timers, idle-shutdown.sh, backup.sh, aws/ssm_run.py
  docs/            plans, runbook, AWS guide, email mockups, this guide
  Dockerfile  docker-compose.yml  pyproject.toml  .env.example
```

**Layering rule:** rules go in `domain` (pure, easy to test), orchestration in `services`, pages in `web`. **New rules never go in templates.** `domain` must not import the database or the network.

**Testing design:** the services take a `Container` of interfaces, so tests plug in fakes (`tests/fakes.py`) instead of
the real NSE, Yahoo, Gemini or Gmail. Rules are versioned (`RULES_VERSION = 2`) and every database change has a migration.

**Job safety:** `services/runs.py` records each run and stage in the database, refuses to start a job that is already
running (3-hour lock), and sends a failure email. A "soft stage" lets one step fail without losing the rest of the job.

---

## 9. The database

SQLite file: `data/app.db` (on the server: `/opt/stockresearch/data/app.db`). Migrations run automatically when the
container starts (`stockresearch init-db`).

| Group | Tables | Holds |
|---|---|---|
| Market data | `stocks`, `daily_prices`, `surveillance`, `screen_candidates` | The stock list with size group and industry, daily prices with delivery %, watch lists, each evening's shortlist |
| Company facts | `fundamentals_snapshots`, `fundamentals_quarterly`, `shareholding_quarterly` | Financials and ownership |
| Evidence | `source_documents`, `events`, `event_sources`, `evidence` | Saved copies of every source, structured events, and the evidence lines on reports |
| Research | `research_queue`, `research_results`, `ai_usage` | The queue, each research pass, daily AI call counts |
| Reports and picks | `reports`, `picks`, `pick_outcomes` | Every rated report, the picks, and stale flags |
| Pretend money | `practice_trades` (the app's), `user_trades` (yours), `rejections` | Trades with rules version, reason it moved; stocks the app said no to |
| Your shares | `holdings`, `holding_reviews`, `alerts` | Your real holdings, daily reviews, alerts (de-duplicated by key) |
| Operations | `runs`, `run_stages`, `backtest_results`, `alembic_version` | Job history and stages |

**Warning:** the `data/app.db` inside the laptop project folder is only an old development copy (12 Nov 2026, early
schema, no picks). The real data lives on the AWS server. Never overwrite the server database with the laptop one.

---

## 10. Settings (environment variables)

Set in a `.env` file (copy `.env.example`). On the server the file is `/opt/stockresearch/.env` (permissions 600).
**Never commit it, paste it into chat, or email it.**

| Setting | Default | Meaning |
|---|---|---|
| `APP_USER`, `APP_PASSWORD`, `SESSION_SECRET` | admin, none, none | Website login. Password 12+ characters, secret 32+ |
| `APP_URL` | http://localhost:8000 | Public address used for links in emails |
| `DOMAIN` | none | Host name Caddy gets an https certificate for |
| `COOKIE_SECURE` | true | Set `false` only on a laptop over plain http |
| `DATABASE_URL`, `DATA_DIR` | sqlite:///data/app.db, data | Storage |
| `GEMINI_API_KEY` | empty | Empty means rules-only mode |
| `GEMINI_MODEL` | gemini-3.5-flash | The AI model |
| `GEMINI_DAILY_CALL_BUDGET` | 200 | AI calls per day |
| `GEMINI_MIN_INTERVAL_SECONDS` | 6 | Gap between AI calls |
| `SHORTLIST_PER_GROUP` | 10 | Stocks rated per size group each day |
| `PICKS_PER_GROUP` | 3 | Maximum new picks per size group |
| `PICK_MIN_RATING` | 3.5 | Minimum rating to be a pick |
| `MIN_TURNOVER_CR` | 5 | Minimum average daily trading, Rs crore |
| `MAX_PLEDGE_PCT` | 25 | Promoter pledge limit |
| `RESEARCH_BATCH_SIZE` | 4 | Stocks per 15-minute research run |
| `ON_DEMAND_DAILY_LIMIT` | 10 | "Research this now" requests per day |
| `BROKERAGE_PER_ORDER`, `DP_CHARGE_PER_SELL` | 20, 15.93 | Cost model |
| `SHORT_TERM_TAX_RATE`, `LONG_TERM_TAX_RATE` | 0.20, 0.125 | Tax model |
| `REFERENCE_INVESTMENT` | 40000 | Typical real trade size for cost estimates |
| `PRACTICE_AMOUNT` | 10000 | Pretend money per pick |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` | Gmail, 587 | Email (Gmail app password) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | empty | Optional Telegram alerts |

Note: `.env.example` still lists the old names `SHORTLIST_SIZE` and `TOP_PICKS`. The code reads `SHORTLIST_PER_GROUP` and
`PICKS_PER_GROUP`; the old names are ignored. Fix the example file when convenient.

---

## 11. Commands

Run on the server as `docker compose exec -T app stockresearch <command>` (from `/opt/stockresearch`), or directly on a laptop.

| Command | Purpose |
|---|---|
| `init-db` | Create or upgrade the database |
| `feasibility` | Check every data source and notifier from this machine. **Run first on any new server** |
| `bootstrap --years N` | First-time load: stock list and price history |
| `collect` | Hourly job |
| `research [--max N]` | Research queue worker |
| `market-close [--date D]` | Evening job |
| `publish` | Morning job and email |
| `open-check` | Flag stale picks after the open |
| `practice` | Update pretend trades from stored prices |
| `practice-weekly` | Send the Friday results email |
| `research-now SYMBOL` | Research one stock now and print its rating |
| `import-holdings FILE [--horizon short\|long]` | Import a broker CSV |
| `backtest --years N` | Replay the price screen on history |
| `backup --out DIR` | Consistent copy of the SQLite database |
| `idle-check` | Exit 0 if the server may switch off (prints why) |

Add `--plain-logs` for human-readable logs (the default is JSON). A job that finds itself already running exits quietly with code 2, which counts as success.

---

## 12. Hosting on AWS, the weekday schedule, backups and cost

### The setup

| Item | Value |
|---|---|
| Account | Personal AWS account `542885031953`. Always use CLI profile **`pavan-personal`**. The laptop's default profile is a **work account that must never be touched** |
| Region | ap-south-1 (Mumbai) |
| Server | One EC2 `t3.small`, Ubuntu, 20 GB disk, instance `i-0fd78de66dca91278`, with a fixed Elastic IP |
| Web address | A free sslip.io name built from that IP (so the IP must not change) |
| On the server | `/opt/stockresearch`, Docker Compose with two containers: `app` (the Python app, 2 workers, 700 MB limit) and `caddy` (https and certificate) |
| Server badge (IAM role) | `stockresearch-server-role`: lets the server talk to Session Manager and one S3 bucket only |
| Backups | S3 bucket `stockresearch-backups-542885031953` (private, encrypted). `backups/` kept 35 days, `deploy/` kept 7 days |
| Start alarm clock | EventBridge Scheduler rule `stockresearch-weekday-start` (role `stockresearch-scheduler-role`) |
| Remote access | **Session Manager only. SSH (port 22) was closed on 22 Nov 2026** |

### Is the "run only on weekdays, start and stop automatically" idea implemented? Yes.

It was requested so the server is not paid for around the clock, because the market only trades on weekdays. It works in two halves:

**Start (by AWS).** The EventBridge rule starts the server at **07:30 IST, Monday to Friday**. Nothing starts it on Saturday or Sunday.

**Stop (by the server itself).** `stockresearch-idle.timer` runs `deploy/idle-shutdown.sh` every 10 minutes (the first time 25 minutes after boot). It asks `stockresearch idle-check` and powers the server off, after a backup, **only when all of these are true**:

1. It is **outside the weekday work window** (Mon-Fri 07:15 to 20:30).
2. **No job is running** (a "running" row older than 3 hours is treated as a leftover).
3. **The research queue is empty** (requests older than 6 hours are ignored).
4. **Nobody has used the website for 30 minutes.**
5. The server has been up **at least 20 minutes**.
6. Anything unclear (for example, uptime cannot be read) means **stay on**: staying on costs a little, switching off early could lose work.

The shutdown only acts if the file `/opt/stockresearch/.auto-shutdown` exists ("armed"); the runbook records that it was armed on 22 Nov 2026. Shutting down from inside "stops" the server (it does not delete it), and the address stays the same.

**Why 07:30 to about 21:00 and not "4 hours before and after the market".** NSE trades 09:15 to 15:30. Four hours either side would be 05:15 to 19:30, but the app's own jobs fall outside that: it needs to be up before the 08:00 morning email, and the 19:00 market-close job and the Friday 19:30 email must run, followed by about two hours of research (4 stocks every 15 minutes, so 30 stocks take about 2 hours). So the real window is about 13.5 hours a day, 67 hours a week instead of 168.

**After a weekend off.** The timers are set to run missed jobs once when the server boots (`Persistent=true`), and `collect` looks back up to 5 days, so nothing announced meanwhile is lost.

**What you give up.** The website is down on evenings after the work is finished, weekends and before 07:30. To use it then, start the server from the AWS console or mobile app (about 2 minutes); it stays on while you use it and stops about 30 minutes after your last visit. Alerts about your holdings that arrive on a weekend go out with Monday's morning email.

**Not built (optional ideas from plan v4):** a "wake-up link" (a small Lambda function) to start the server from a web link, and a heartbeat that warns you if no morning email was sent by 09:00. If the 07:30 start ever fails, there is no morning email and nothing warns you.

**To start the server by hand:** AWS console, EC2, Instances, select it, Instance state, Start. Or:
`aws --profile pavan-personal --region ap-south-1 ec2 start-instances --instance-ids i-0fd78de66dca91278`

**To undo the schedule:** delete the rule (`aws scheduler delete-schedule --name stockresearch-weekday-start --profile pavan-personal --region ap-south-1`) and delete `/opt/stockresearch/.auto-shutdown`. The server then runs around the clock again.

**Order matters:** create and test the start rule *before* arming the shutdown, or the server would switch off and nothing would switch it on.

### Backups

`deploy/backup.sh` makes a consistent copy of the database (SQLite's own backup method), keeps 30 days on the server, and syncs to the S3 bucket when `/opt/stockresearch/.backup.env` sets `BACKUP_S3_URI`. It runs before every self-shutdown and also from a nightly 02:30 timer (the plan wanted it moved to the evening; the self-shutdown backup covers that, and a missed nightly run catches up at boot). A copy to your laptop once a month is a cheap extra safeguard.
**Restore:** stop the app, copy a backup over `data/app.db`, start the app (steps in `RUNBOOK.md` section 6).

### Deploying a new version (no SSH)

1. Package the project into a tarball and upload it: `aws --profile pavan-personal s3 cp app.tgz s3://stockresearch-backups-542885031953/deploy/app.tgz`
2. Run a deploy script on the server through Session Manager: `python deploy/aws/ssm_run.py deploy_script.sh 1200`. The helper refuses to run unless the profile is the personal account.
3. On the server the script downloads the package, unpacks it in `/opt/stockresearch`, runs `docker compose up -d --build app`, and reinstalls the timers (`deploy/systemd/install-timers.sh`). Database migrations run automatically when the container starts.

**The firewall:** only ports 80 and 443 are open. If SSH is ever needed again, add a port 22 rule for your current address in the security group `stockresearch-web` and remove it afterwards. Ask before changing the firewall.

### Cost and the deadline

| | Always on | Weekday schedule (current) |
|---|---|---|
| Server hours | about $16 a month | about $6.5 a month |
| Fixed public address | $3.65 | $3.65 |
| Disk (estimate) | about $2 | about $2 |
| **Total** | **about $22** | **about $12** |

(Prices are from third-party lists and an estimate for the disk; confirm on the AWS pricing and Billing pages.)

**Billing alerts** (created 15 Nov 2026 with `aws budgets create-budget`): `stockresearch-no-real-money` (emails on any charge credits do not cover) and `stockresearch-credit-burn` ($25 a month on usage before credits, alerts at 50%, 80%, 100% and forecast). A third budget `stockresearch-TEST-delete-me` was created only to prove the emails arrive and should be deleted. Alerts mention amounts only.

**Important deadline: the AWS free plan ends 15 Feb 2027** (per the screenshot noted in plan v4; confirm on the Billing page). After that AWS **closes the account unless you upgrade** to a paid plan, and erases data after 90 days. A decision is needed before then: upgrade (remaining credits carry on, bill after they run out) or move the app. The app is Docker plus one SQLite file, so moving is easy. Oracle Cloud Always Free is the main free alternative. Keep backups off AWS as well.

---

## 13. Running it on a laptop, tests and quality checks

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows. Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
copy .env.example .env                 # then fill it in; COOKIE_SECURE=false for plain http
stockresearch init-db
stockresearch feasibility
stockresearch bootstrap --years 1
stockresearch market-close
stockresearch research --max 5
stockresearch publish
uvicorn stockresearch.web.app:app_factory --factory --port 8000     # open http://localhost:8000
```

**Quality checks** (the same ones the CI workflow in `.github/workflows/ci.yml` runs on every push):

```bash
ruff check src tests          # lint
mypy                          # type checks
pytest --cov=stockresearch    # tests; CI requires at least 75% coverage; it also builds the Docker image
```

Test files: `tests/domain/` (rules), `test_adapters.py` (parsing of NSE, Yahoo, news), `test_pipeline.py` (end to end with fakes), `test_practice_flow.py` (pretend trades, pages, both emails), `test_track_record.py`, `test_web.py`, `test_jobs.py`, `test_idle.py` (shutdown rules).

---

## 14. Status: what is done, what is live, what is open

### Built (in the code, per this review)

- The full pipeline: collect, research with validated AI, rating, gates, picks per size group, no-repeat rule.
- App's track record with Rs 10,000 pretend trades (rules version 2), "why it moved", stocks the app said no to, weekly verdict.
- Your own practice portfolio, holdings import and review, exit alerts, stale-pick check.
- The website (all pages in section 6) with login and security headers.
- Redesigned morning and weekly emails (22 Nov 2026).
- Weekday start, idle self-shutdown, S3 backups, Session Manager deploys (plan v4).
- Tests, lint, type checks, CI workflow, Docker build.

### Live (per `RUNBOOK.md`, dated 22 Nov 2026 — not independently verified)

The runbook records the app as deployed on the AWS server, billing budgets created, the S3 bucket, roles and weekday schedule created, SSH closed and self-shutdown armed on 22 Nov 2026.

### Not verified at the time of writing, and how to check

| Question | How to check |
|---|---|
| Is the server up and the app healthy? | Open the site's `/health` page (it returns a small JSON), or look at the **System** page (`/runs`) for recent runs |
| Does the live server have the 22 Nov email redesign? | Check the files' dates in the next deployed package, or just look at the next morning email (summary table, tiles) |
| Does the start rule exist and fire? | `aws scheduler get-schedule --name stockresearch-weekday-start --profile pavan-personal --region ap-south-1` and check the server started at 07:30 |
| Is self-shutdown armed? | `ls /opt/stockresearch/.auto-shutdown` on the server (through Session Manager); `journalctl -u stockresearch-idle -n 20` |
| Do the tests pass right now? | `pytest` on the laptop |
| Were the exposed secrets replaced? | Plan v4 says the Gemini key, the AWS access key and the app password were shown in chat and must be replaced. Confirm they were rotated |
| Was the TEST budget deleted? | `aws budgets describe-budgets --account-id 542885031953 --profile pavan-personal --region us-east-1` |

### Open items and known gaps

1. **Decide before 15 Feb 2027:** upgrade AWS or move (section 12).
2. **Paper trade for 4 to 8 weeks** and review the track record (are 4 and 5 star picks better than 3 star? is the average after costs positive? how do "excitement" picks behave?) before using real money. Then review the scoring weights together.
3. **Replace exposed secrets** and delete the TEST budget, if not already done.
4. **Probably not under version control:** no `.git` folder was found in the project folder (plan v3 also said it was not a git repository). Putting it under git, with `.env` and `.env.server` excluded, is recommended in plan v3.
5. **Small clean-ups:** `.env.example` has stale setting names; the README's opening still says "up to five stocks" (plan v3 made it up to three per size group); `alerts.html` was not redesigned; the nightly backup timer is still 02:30.
6. **Optional:** a heartbeat warning if no morning email by 09:00; a Lambda wake-up link; a CloudWatch status alarm.
7. **Known data limits:**
   - Financial statements come from Yahoo Finance, not NSE's XBRL files (not parsed).
   - Mutual fund and foreign-investor breakdown is not read; the institutional total plus fund bulk/block deals are used.
   - NSE's prices are not adjusted for splits and bonus issues, so a recent split can show a false fall.
   - Ownership data is quarterly, up to three months old; dates are shown beside every figure.
   - NSE may block some cloud servers; if so, prices fall back to Yahoo and filings/ownership would be missing (run `feasibility` on a new server).
   - Google News links go through a redirect; the real publisher is checked against the allowlist first.
8. **It may not make money.** That is the point of the pretend trading.
9. **Legal:** for personal use only. Sharing or selling the picks to others requires SEBI registration as a Research Analyst or Investment Adviser.

---

## 15. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| No morning email | Is the server up at 08:00? Check the **System** page. Run `feasibility`. Gmail needs an app password. If the 07:30 start failed, start the server by hand |
| Website unreachable | The server is probably off (outside the work window). Start it from the AWS console or app and wait about 2 minutes |
| Feasibility says NSE checks FAIL (403 or timeouts) | NSE blocks some cloud IP ranges. Try Mumbai (most likely to work), Oracle Cloud, or run `collect` and `market-close` from a home machine. Yahoo keeps prices working but filings and ownership need NSE |
| "Login is disabled" | `APP_PASSWORD` under 12 characters or `SESSION_SECRET` under 32 |
| Login works on a laptop but not the server | `COOKIE_SECURE=true` needs the `https://` address |
| "Daily AI budget used up" in reports | Lower `SHORTLIST_PER_GROUP` or raise `GEMINI_DAILY_CALL_BUDGET` within your free limit |
| Certificate errors | The domain must point at the server and ports 80 and 443 must be open; see `docker compose logs caddy` |
| Out of memory during a build | Check the swap file is active: `swapon --show` |
| A job failed | You get an email; details are on the System page. On the server: `journalctl -u stockresearch-job@publish -n 100`, `docker compose logs --tail 200 app` |
| Server never switches off | Check `.auto-shutdown` exists, and `journalctl -u stockresearch-idle -n 20` to see the reason it gave |

---

## 16. Rules for anyone working on this (including Claude)

- **Never paste keys or passwords into chat, documents or commits.** They live only in the server's `.env`. Do not read or print `.env` or `.env.server` unless the owner asks.
- **AWS:** always `--profile pavan-personal`. The default profile is a different (work) account that must never be touched. Ask before changing the firewall.
- **Numbers come from code, never from the AI.** The rating, the safety price, the pretend-trade results and the reasons are computed by fixed rules.
- **Rules are versioned and trades are append-only.** Changing a rule means a new `RULES_VERSION`; old trades keep the old one and are never edited.
- **Never weaken the trust model:** no new source is read unless it is added to the allowlist in `domain/sources.py`, with a test.
- **Pages never call outside services;** only the jobs do.
- **Every database change needs a migration** and every rule needs a test. Run `ruff`, `mypy` and `pytest` before deploying.
- **The app never places orders and gives no "buy" or "sell" instructions for the owner's real shares.** Keep the disclaimer in every email and page.
- **Do not overwrite the server's database** with the laptop's.
- **Read this guide and `docs/RUNBOOK.md` before changing deployment.** Update this guide when behaviour changes.

---

## 17. History and where the other documents fit

| Date (2026) | What happened | Document |
|---|---|---|
| 12 Nov | Original design and first build (trust model, scoring, picks, track record, website, deployment on AWS) | `docs/PLAN.md` |
| 12 Nov | Plain-language wording, glossary and the practice portfolio | `docs/PLAN-v2-practice-and-plain-language.md` |
| 13 Nov | Picks by company size, no repeats, Rs 10,000 per pick, the app's own track record with reasons, stocks it said no to, table-first emails | `docs/PLAN-v3-track-record-and-email.md`, `docs/mockups/email-options.html` |
| 15 Nov | AWS cost plan: weekday schedule, billing alerts, off-server backups; AWS permissions guide; billing budgets created | `docs/PLAN-v4-aws-cost-and-schedule.md`, `docs/AWS-SETUP-GUIDE.md`, `docs/RUNBOOK.md` section 11 |
| 22 Nov | Plan v4 built and live: S3 backups, roles, weekday start, self-shutdown, SSH closed, Session Manager deploys. Email templates redesigned for readability. This guide written | `docs/RUNBOOK.md` sections 10 to 12 |

| Document | Read it when |
|---|---|
| `README.md` | You want the short overview and quick start |
| `docs/PLAN.md` | You want the full original reasoning and the milestones |
| `docs/PLAN-v2…`, `v3…`, `v4…` | You want the reasoning behind a specific change |
| `docs/RUNBOOK.md` | You are setting up, running or fixing the server |
| `docs/AWS-SETUP-GUIDE.md` | You are setting up AWS permissions for the first time |
| `docs/mockups/email-options.html` | You want to see the email layout options that were considered |

---

## 18. Plain-words glossary

| Word | Meaning |
|---|---|
| **Pick** | A stock the app suggests considering today. An option, not an instruction |
| **Rating** | The app's 1 to 5 score. 3.5 or more is needed to be a pick |
| **Label** | Whether the rise looks driven by excitement, business results, or a mix |
| **Trigger** | The specific recent news that could move the price (an order, results, a stake purchase) |
| **Verified** | Confirmed by an official filing, or by two different newspapers |
| **Filing** | A document a company must legally give the stock exchange |
| **Safety level / stop** | A price at which to sell if the share falls, to limit the loss |
| **Holding window** | How long to hold before reviewing or selling |
| **Delivery %** | The share of traded shares actually taken home, not bought and sold the same day. Rising delivery suggests real buying |
| **Promoter** | The founders or controlling owners of a company |
| **Pledged shares** | Shares the owners have used as security for a loan; many pledged shares mean forced-selling risk |
| **Surveillance list (ASM/GSM)** | An exchange watch list for over-speculated shares |
| **Upper circuit** | The most a share may rise in a day; at that limit buy orders may not fill |
| **Nifty 50** | An index of India's 50 biggest companies, used as the yardstick |
| **Large / mid-size / small** | Size groups from NSE's lists (Nifty 100, Nifty Midcap 150, the rest) |
| **Pretend money** | Practice trades with real prices, costs and tax but no real money |
| **Market day** | A weekday the stock market is open |
| **IST** | India Standard Time, used for every schedule here |
