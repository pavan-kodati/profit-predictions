# Stock Research Assistant: Architecture and Delivery Plan

Status: **draft for approval**. Nothing in this plan is built until you approve it.
Date: 2026-11-12 (revision 2: adds continuous research, evidence links, portfolio review)

## 1. What we are building, and why

A personal web application that researches the Indian stock market around the clock and, every trading
morning, emails you up to five stocks that have a credible, verified reason to move up over the next few days
to three weeks. Each stock gets a rating from 1 to 5 and a report that shows the homework behind the rating,
with a link to every source. It also reviews the shares you already own.

**It is:** a research assistant that gathers facts from trusted sources, scores them with fixed rules,
explains itself with citations, and keeps an honest record of how its past picks did.

**It is not:** a predictor, a guarantee, an auto-trader, or investment advice. It never places orders.

### Goals

| # | Goal | How we will know it is met |
|---|---|---|
| G1 | Up to five rated picks by 8:00 am IST every trading day | Email arrives on time on 95% of trading days |
| G2 | Every claim is traceable to a trusted source | No statement in a report without a link to an approved source |
| G3 | Tell hype from substance | Each pick is labelled *hype-driven*, *mixed* or *business-backed*, with a holding window |
| G4 | Prove whether it works | A track-record page shows the real outcome of every pick, by rating |
| G5 | Zero running cost | Only free and open-source components; see section 9 for the one caveat |
| G6 | Research is continuous, not a morning rush | Filings are picked up within an hour of publication; the morning list only ranks what is already stored |
| G7 | Review the shares you already own | Each holding shows a health rating, what changed, and a status with evidence |

### Out of scope for version 1

Futures and options, intraday trading, automatic order placement, multiple users, mobile app.

## 2. The trust model for news (your main requirement)

A random post saying "this stock will go up 20%" must never influence a rating. The design enforces this in
three ways.

### 2.1 Only approved sources are read

The app keeps an explicit allowlist. Anything not on it is ignored.

| Tier | What it is | Sources | How it is used |
|---|---|---|---|
| **1. Official record** | Legally required disclosures. Companies face penalties for false filings. | NSE and BSE corporate announcements, quarterly results, shareholding patterns, bulk and block deals, insider-trade disclosures, company investor presentations | The only tier that can *establish a fact* |
| **2. Established financial press** | Newsrooms with editors and corrections policies | Economic Times, Mint, Business Standard, Moneycontrol, CNBC-TV18, Hindu BusinessLine, Financial Express, Reuters, PTI, NDTV Profit | Can establish a fact only when **two independent outlets** report it |
| **3. Opinion** | Views, not facts | Broker research notes and target prices | Shown as "what analysts say". Counts a little towards sentiment, never towards the trigger |
| **Blocked** | Unverifiable | Social media, Telegram, YouTube, WhatsApp forwards, blogs, forums, AI-written news sites, anything not listed above | Never fetched, never shown to the AI |

On broker and platform recommendations: I recommend treating them as opinion only (Tier 3). Brokers earn
when you trade, their "buy" calls far outnumber their "sell" calls, and a target price is a view, not a fact.
Research platforms such as Screener, Trendlyne and Tickertape have no free API and their terms forbid
scraping, so the app will not depend on them. It goes to the same primary filings they use.

### 2.2 A trigger must be verified before it counts

Each piece of news becomes a structured **event** (type, date, value, source links) with a status:

- **Verified**: backed by a Tier 1 filing, or by two independent Tier 2 outlets.
- **Reported**: one Tier 2 outlet only. Shown, labelled "single source", scores at half weight.
- **Unverified**: anything else. Not scored. A stock whose only trigger is unverified is capped at 3 stars.

### 2.3 The AI is a reader, not a source

- Gemini is given only documents the app fetched from approved sources. It is not allowed to search the open
  web. This also removes any dependence on paid search features.
- It must quote the passage and document it took each fact from. A claim without a quote is discarded.
- Every number it extracts (order value, profit, holding %) is checked against the structured data where
  that exists. Mismatches are dropped and logged.
- The 1-5 rating is computed by code from the scores. The AI supplies evidence and explanation only.

### 2.4 Evidence you can check yourself

Every reason behind a rating is shown as an evidence line with five parts:

| Part | Example |
|---|---|
| Claim | Won a Rs 450 crore order from a defence customer |
| Link | Direct URL to the exchange filing or article |
| Quote | The exact sentence in that document the claim rests on |
| Why it is trusted | "Official NSE filing", or "Reported by Economic Times and Mint" |
| Date | When it was published |

Rules:

- **Saved copies.** Articles get edited and links break. The app stores its own copy of each document as it
  was when read, so a rating can be audited months later.
- **Evidence against is shown too.** Each report lists the negative items found (a weak quarter, promoter
  selling) with the same five parts. A report with only supporting links would be a sales pitch.
- **"Not found" is stated.** If the app looked for order book or fund holding data and found nothing
  trustworthy, the report says so.
- The morning email carries the top two or three links per stock. The report page carries all of them.

A link proves the fact is real. It does not prove the stock will rise: a genuine order win can already be
in the price. The track record (G4) is what shows whether verified facts led to gains.

## 3. How a stock is judged

### 3.1 Stage A: safety gates (a stock that fails is dropped)

| Gate | Why |
|---|---|
| Average daily traded value below Rs 5 crore | You could not sell quickly |
| On the exchange surveillance lists (ASM, GSM) or in trade-for-trade series | Exchanges put over-speculated stocks here; they can lock at the lower limit so you cannot exit. Hype stocks often land here |
| Locked at the upper circuit | You could not actually buy it |
| Promoter shares pledged above 25% | Forced selling risk |
| Results due inside the holding window | Flagged, not dropped: the outcome is a coin toss |

### 3.2 Stage B: six scores, each 1 to 5

| Area | Weight | What is measured | Source |
|---|---|---|---|
| Business quality | 25% | Sales and profit growth over 5 quarters and 3 years, margins, debt, return on capital, valuation | Quarterly results |
| Price and volume | 20% | Trend, unusual volume, distance from one-year high, **delivery percentage** (shares actually taken home versus traded intraday; rising delivery means real buying) | NSE daily files |
| Trigger | 25% | Verified events in the last 10 days: order wins, results beat, expansion, approvals, stake purchases | Tier 1 and 2 |
| Order book | 10% | Disclosed order wins and pending orders compared with yearly sales | Filings, presentations |
| Ownership | 10% | Promoter holding and its change, pledges, mutual fund and foreign holding changes, insider buying, bulk deals | Shareholding filings |
| Sentiment | 10% | Tone of Tier 2 coverage and Tier 3 analyst views | Tier 2 and 3 |

Areas with no data are left out and the rest re-weighted. Each report shows a **data completeness** figure
so a rating built on thin data is visible.

### 3.3 Stage C: hype or substance

| Label | Signals | Holding guidance shown |
|---|---|---|
| **Hype-driven** | Price has run ahead of profits, trigger is a story about the future, volume up but delivery % down, weak business score | 3-5 days, with exit rules |
| **Mixed** | Real verified trigger, but the price already reflects much of it | 1-2 weeks |
| **Business-backed** | Verified trigger plus strong business score, price not yet stretched | 2-3 weeks |

This is the Azad Engineering case: a real company with a future-looking story, where the price ran on
excitement. The app would have labelled it hype-driven or mixed and told you to plan the exit first.
It cannot tell you the day the excitement ends.

### 3.4 Caps

Unverified trigger: maximum 3 stars. Up more than 15% in five days: maximum 3 stars (late entry).
Data completeness below 60%: maximum 3 stars.

### 3.5 Portfolio review

The same research engine, pointed at shares you already own. You upload the holdings file your broker lets
you export, or type the holdings in, and tag each one **short-term** or **long-term**.

| Shown for each holding | Detail |
|---|---|
| Health rating | The 1-5 score with reasons. Long-term holdings are scored on business quality, ownership and order book only; the 1-3 week price rules do not apply to them |
| What changed since you bought | New filings, results, holding changes, negative events, each with an evidence line (2.4) |
| Status | **Case intact**, **Weakening** or **Broken**, with the evidence behind it |
| Averaging check | When a holding is below your buy price: is the fall market-wide or specific to the company, and do the business numbers still hold up. Averaging into a stock whose business has worsened is the most common way a small loss becomes a large one |
| Tax timing | Gains on shares sold after 12 months are taxed at 12.5% instead of 20%. A holding close to that date is flagged |
| Concentration | Share of your money in one stock and in one sector |

Boundaries:

- **Evidence, not orders.** The app says "Broken: profit fell two quarters running, promoter sold 3%".
  It does not say "sell". The keep, sell or average decision is yours.
- **No broker connection.** The app never logs in to your trading account.
- **Privacy.** Quantities and amounts stay in your database. Only company names are ever sent to Gemini.

## 4. Recommended features beyond what you asked for

| # | Feature | Why it matters |
|---|---|---|
| E1 | **Holdings monitor with exit alerts** | You mark "I bought this". The app watches it daily and emails you when volume dries up, the price closes below its short-term average, a negative filing appears, or the stop level is hit. Getting out is the hard part of hype trades |
| E2 | **Track record with costs** | Short-term gains on shares are taxed at 20%, plus brokerage, transaction tax and demat charges. The scorecard shows returns after these, so a "10%" is not overstated |
| E3 | **Realistic entry price** | Picks are scored from the next morning's opening price, not the previous close. If a stock opens more than 3% higher, the email flags the pick as stale |
| E4 | **Back-test of the price rules** | Before going live, run the price and volume rules over three years of history to see if they had any edge. The news part cannot be back-tested, which is what paper trading is for |
| E5 | **Paper trading first** | 4-8 weeks of recorded picks with no real money, then a review |
| E6 | **Negative-event watch** | Auditor resignation, regulator action, promoter selling, pledge increase: any of these removes a stock from picks |
| E7 | **Risk calculator** | For an amount you enter, shows the loss in rupees if the stop level is hit. It informs your decision; it does not make it |
| E8 | **Telegram alerts (optional)** | Free, faster than email for exit alerts |
| E9 | **Failure alert** | If the morning run fails or a data source breaks, you get an email saying so instead of silence |
| E10 | **Research on demand** | Type any NSE stock name at any time and get its report, not only the ones the app chose. Limited to a few per day by the free AI allowance |
| E11 | **Building-story detection** | Because evidence accumulates, the app can see several verified events for one company over two weeks, which is a stronger case than a single headline |

## 5. Architecture

A **modular monolith**: one deployable application with strict internal boundaries. For a single user on a
1 GB server this is the right size. Microservices would add cost and failure points with no benefit.

```
   COLLECT (all day, hourly)          RESEARCH (all day, queue)          PUBLISH (08:00 IST)
   [ingest filings, news, prices] --> [verify events] -> [AI extract] --> [gate, score, rank] -> email
              |                                 |                                  |
              +---------------------------- database ------------------------------+
              |                                                                    |
   adapters (one per source, behind an interface)                       web app (FastAPI) -> you
              |                                                          picks, reports, portfolio,
   NSE files | BSE/NSE filings | Yahoo (fallback) | Tier 2 RSS | Gemini | SMTP     research on demand
```

The three parts are independent jobs that only share the database. If collection or research is late or
fails, the morning list is still produced from what is stored, and says how fresh its data is.

### 5.1 Layers and rules

| Layer | Contains | Rule |
|---|---|---|
| `domain` | Scoring, gates, event verification, hype classification | Pure functions. No network, no database. Fully unit tested |
| `ports` | Interfaces: `PriceSource`, `FilingSource`, `NewsSource`, `Researcher`, `Notifier` | The domain depends only on these |
| `adapters` | One implementation per external source | Timeouts, retries, rate limits, response validation. Swappable without touching the domain |
| `services` | The pipeline stages and the tracker | Each stage is idempotent and records its status |
| `web` | Pages and a small JSON API | Reads the database only. Never calls external sources during a page load |
| `jobs` | Command-line entry points the scheduler calls | |

### 5.2 Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12, fully type-hinted | Best data libraries |
| Web | FastAPI, server-rendered pages (Jinja2 + HTMX) | No separate front-end build to host or maintain |
| Database | SQLite in WAL mode, SQLAlchemy 2, Alembic migrations | Free, zero admin, enough for one user. Moving to PostgreSQL later needs no code changes |
| Validation | Pydantic models at every external boundary | Bad data fails loudly at the edge |
| AI | Gemini API, free tier, behind the `Researcher` interface | Swappable; about 20-25 calls per day |
| Scheduling | systemd timers calling a CLI: hourly collect, hourly research, daily publish | Simpler and more reliable than an in-app scheduler |
| Work queue | A `research_queue` table in the database | Spreads AI calls across the day inside the free limit; no extra software |
| Packaging | Docker Compose | Same on your laptop and on the server |
| HTTPS | Caddy with a free Let's Encrypt certificate and a free DuckDNS name | No cost |
| Quality | ruff, mypy, pytest, GitHub Actions on every push | All free |

Antigravity is a coding tool and cannot be called by an app. The app needs its own Gemini API key from
Google AI Studio, which is free.

### 5.3 Data model (main tables)

`stocks`, `daily_prices`, `fundamentals_quarterly`, `shareholding_quarterly`, `source_documents`
(every fetched filing or article, with URL, tier, hash and a saved copy of the text), `events` (typed, with
verification status and links to `source_documents`), `evidence` (claim, quote, document, for or against),
`research_queue`, `runs` and `run_stages` (audit trail), `reports` (scores, reasons, evidence),
`picks` and `pick_outcomes` (track record), `holdings` (with buy price, date and short-term or long-term tag),
`holding_reviews` and `alerts`.

Every report can be reproduced from stored documents, so you can always see exactly what the app knew when
it made a call.

### 5.4 Production standards

- **Security:** login required, HTTPS only, server firewall open only on 443 (and SSH from your IP),
  secrets in environment variables and never in the repository.
- **Reliability:** every stage retries with backoff; one failing source degrades the report and is stated in
  it; the run never fails silently (E9).
- **Observability:** structured logs, a run-history page with per-stage timings, a health-check endpoint.
- **Backups:** nightly copy of the database to S3 (5 GB free), 30 days kept.
- **Testing:** unit tests for all domain rules, recorded-response tests for each adapter, one end-to-end test
  on a fixed dataset. Target 85% coverage on `domain` and `services`.

## 6. How it runs through the day

Research is continuous. The morning email is only the delivery of what has already been gathered.

| When (IST) | Job | What it does |
|---|---|---|
| Every hour, all day | **Collect** | Fetch new exchange filings and Tier 2 headlines; store each document; create events. Most filings appear in the evening after the market closes |
| Every hour, all day | **Research** | Take the next few items from the queue (new events on candidate stocks, your holdings, on-demand requests); verify; Gemini extracts evidence. Paced to stay inside the free AI limit |
| 19:00 | **Market close** | Download the official NSE end-of-day file (prices, volume, delivery %), bulk and block deals, surveillance lists; re-screen about 750 stocks to refresh the candidate list |
| 08:00 | **Publish** | Apply gates, score, classify, apply caps, pick up to five; send the email; refresh portfolio reviews and exit alerts |
| 09:20 | **Open check** | Record actual opening prices; flag stale picks |
| Any time | **On demand** | You request a report on any stock, or open the dashboard, which always shows the latest stored research |

The publish time is a setting. Nothing in the design depends on it being 08:00.

"Up to five": on a day when nothing clears the bar, the email says so. Forcing five picks every day would
mean recommending weak ones.

## 7. Delivery plan

Each milestone ends with something you can check. **You** = Pavan; **Claude** = me, writing code with you reviewing.

| # | Milestone | Deliverable and acceptance test | Who |
|---|---|---|---|
| M0 | **Feasibility check on the real server** | A one-page result: can the EC2 instance reach each data source, and what are the actual Gemini free limits on your key. *This comes first because NSE is known to block cloud servers; if it does, we switch to the fallback in section 9 before building anything on top* | You: create EC2 instance and Gemini key. Claude: test script |
| M1 | Foundation | Repository, layers, config, database migrations, CI green, login page | Claude |
| M2 | Market data and screening | Daily prices and delivery % stored; gates and screen produce a candidate list; back-test report (E4) | Claude |
| M3 | Fundamentals and ownership | Quarterly results and shareholding stored; quality and ownership scores match hand-checked values for 10 stocks | Claude; you spot-check |
| M4 | Trusted news and events | Hourly collection; allowlist enforced; documents saved with a copy; events created with verification status; test proves a blocked source can never reach a report | Claude |
| M5 | AI research with evidence | Research queue; evidence lines with claim, link, quote, tier and date, for and against (2.4); test proves uncited or mismatched claims are dropped | Claude |
| M6 | Rating and dashboard | Today's picks, full report page with evidence, research on demand (E10), run-history page | Claude; you review wording and layout |
| M7 | Email and track record | Morning email with top links; scorecard with after-cost returns (E2, E3) | Claude; you: Gmail app password |
| M8 | Holdings monitor | "I bought this" and exit alerts (E1, E6, E7) | Claude |
| M9 | Portfolio review | Holdings upload; health rating, status, averaging check, tax timing, concentration (3.5); test proves quantities never leave the database | Claude; you: export your holdings file |
| M10 | Deployment | Running on the server with HTTPS, backups, failure alerts (E9), runbook | Claude writes; you run the commands |
| M11 | Paper trading | 4-8 weeks live with no real money, then a joint review of the scorecard and rule tuning | You |

## 8. Risks, stated plainly

| Risk | Effect | Mitigation |
|---|---|---|
| **The approach may not make money** | Public news is often already in the price by the next morning | M11 measures this before real money is used. Be ready for the answer to be "no edge" |
| NSE blocks requests from AWS | No filings or official prices on the server | Checked in M0. Fallbacks: BSE feeds, Yahoo for prices, or running the fetch step from GitHub Actions |
| Free data sources are unofficial and can change | A source breaks without notice | Adapters are isolated; failure alert; each source has a fallback |
| AI misreads a document | Wrong fact in a report | Quote-required rule, number cross-checks, rating computed by code |
| Order book and mutual fund data are patchy | Those scores are often missing | Shown as "not available" and excluded from the rating, never guessed |
| Ownership data is quarterly | Up to three months old | Date shown beside every figure; daily bulk deals and insider disclosures fill part of the gap |
| Gemini free limits shrink | Fewer stocks researched | Shortlist size is a setting; rule-based scores still work without AI |

## 9. Cost: the one caveat

Everything is free software and free data. The server is the caveat. AWS accounts created before
mid-July 2025 get 12 months of a small free instance; newer accounts get credits that run out, typically
within six months. After that an EC2 instance costs roughly $8-10 a month. Please check which applies to
your account. If it would cost money, two alternatives stay free indefinitely: Oracle Cloud's always-free
server, or running the daily job on GitHub Actions. Because the app is packaged with Docker, the choice does
not affect the code.

Note also that Google may use free-tier Gemini requests to improve its products. Nothing personal is sent,
only public company documents.

## 10. One legal point

This is fine as a personal tool. If you ever share the picks with other people or charge for them, Indian
law requires registration with SEBI as a Research Analyst or Investment Adviser. Keep it private unless you
go down that route.

## 11. Decisions I need from you

| # | Decision | My recommendation |
|---|---|---|
| D1 | Approve this plan, or tell me what to change | |
| D2 | Hosting: EC2, Oracle always-free, or GitHub Actions | Decide after you check your AWS free-tier status (section 9) |
| D3 | Alerts: email only, or email plus Telegram | Email plus Telegram for exit alerts |
| D4 | The prototype files I wrote before you stopped me (`app/`, `run_scan.py`, `requirements.txt`, `.env.example`) | Delete them when M1 starts. They were useful only to confirm the free data sources respond, and they do not follow this architecture |
