# Plan v2: Practice portfolio and plain-language experience

Status: **draft for approval**. Date: 2026-11-12. Builds on [PLAN.md](PLAN.md).

## 1. The problem, from the product side

The app works, but it speaks like an analyst. Words like *delivery %, promoter pledge, ASM, trigger, completeness*
mean nothing to someone new to the market. And the most important question, **"would following this have made me
money?"**, is answered only by a table of percentages on the Track record page.

### Who we are designing for

| Person | Knows | Needs |
|---|---|---|
| **Beginner** (primary) | What a share is, roughly. Not what a P/E ratio is | To understand each pick in a minute, and to see in rupees whether the tool works |
| **Pavan as investor** | Basics, invests about Rs 40,000 at a time | Clear "buy around, sell if, review on" guidance and honest results before using real money |
| **Pavan as checker** | Wants to verify claims | The detailed scores and sources, which stay available one click away |

### Goals and how we'll measure them

| # | Goal | Done when |
|---|---|---|
| P1 | A beginner understands a pick without help | Every pick reads as three plain sentences: why it might rise, what could go wrong, what to do if you buy |
| P2 | Practice results are understandable in 10 seconds | The Practice page opens with one sentence in rupees and a comparison with a fixed deposit and the Nifty 50 |
| P3 | Results are honest | The rules are fixed before any trade, written on the page, and past trades are never edited. Costs and tax are always included |
| P4 | No jargon without an explanation | Every technical word has a tap-to-explain definition |
| P5 | Nothing is lost for experts | Detailed scores, evidence and numbers remain under "Show details" |

## 2. Feature A: Practice portfolio (paper trading)

"Paper trading" becomes **Practice portfolio** in the app, because that's what it is.

### The rules (fixed, shown on the page)

1. **Buy:** every pick is bought with pretend money at the **first opening price after it was published**, the
   price a real person would actually get. Picks published at 08:00 buy at that morning's 09:15 open; a pick published
   after the open buys at the next day's open. Amount per pick: decision D1.
2. **Sell**, whichever comes first:
   - the price falls to the **stop level** shown in the report (protects against big losses);
   - the **holding window** ends: 5 trading days for quick trades, 10 for mixed, 15 for business-backed;
   - optional: the price reaches **+10%** (decision D2).
3. **Costs:** brokerage, government taxes, exchange charges and 20% short-term gains tax are deducted from every
   trade, using the same cost model as the rest of the app.
4. **No hindsight:** a trade uses only prices that existed on that day. Rules carry a version number. If the rules
   change later, old trades keep the rules they were made under.

### What the page shows (top to bottom)

1. **One-sentence answer** in rupees. For example: "If you had followed every pick since 13 Nov, Rs 40,000 would now
   be Rs 41,240 (+3.1%) after all costs."
2. **Verdict badge**, with honest thresholds:
   - *Too early to tell*: fewer than 20 finished trades or under 4 weeks. Luck dominates small samples.
   - *Not beating the market*: behind the Nifty 50 over the same period.
   - *Beating the market so far*: ahead of the Nifty 50, with the trade count shown.
3. **Comparison chart**: practice portfolio vs the Nifty 50 index vs a fixed deposit at 7% a year, all starting from
   the same amount. A beginner understands "better or worse than an FD" instantly.
4. **Four plain numbers**: trades finished, how many made money, biggest win, biggest loss (in rupees).
5. **Does the rating mean anything?** Simple bars: "Strong case (4-5): 6 of 8 made money. Mixed (3): 2 of 5."
6. **Trades as short stories**: "Bought Divi's Labs on 13 Nov at Rs 6,210. Sold on 27 Nov at Rs 6,540 when the
   2-week window ended. Made Rs 480 after costs." Open trades show "Holding: up Rs 210 so far, sells by 27 Nov."

### Weekly email

Every Friday evening: "Your practice portfolio this week": the one-sentence answer, the week's finished trades and
the verdict. This is the habit that replaces checking the Track record page by hand.

## 3. Feature B: Plain-language experience

### Principles

- **Rupees before percentages.** "On Rs 40,000 you could lose about Rs 2,900" instead of "-7.2%".
- **Decisions before data.** Each pick starts with what to do and why; the scores sit underneath.
- **Every term explained once, everywhere.** A small (i) next to a word opens a two-line definition.
- **Same meaning, same colour:** green = good, amber = caution, red = risk, on every page.
- **Progressive disclosure:** simple by default, "Show details" for experts.

### Changes

| Today | After |
|---|---|
| "4.4 / 5" | "4.4 / 5, strong case", with a legend: 5 very strong, 4 strong, 3 mixed (wait), 2 weak, 1 avoid |
| "Business-backed / Mixed / Hype-driven" | "Backed by business results / Some real news, some excitement / Mostly excitement: quick trade only" |
| Score grid first on the report | **In plain words** box first: why it might rise, what could go wrong, if you buy (buy around, sell if it falls to, review on), then the detailed scores under "Show details" |
| Stop level in rupees per share | "If you put in Rs 40,000 and it falls to the stop level, you'd lose about Rs 2,900" |
| Jargon in reasons ("delivery %", "ASM", "pledge") | Rewritten reasons plus the (i) glossary |
| No introduction | First-visit welcome (3 short cards: what this is, how to read a pick, why practise first), plus a **How it works** page |
| Track record table | Kept for experts, linked from the Practice page |

The glossary covers about 25 terms: share price, NSE, promoter, pledge, institutional investors, delivery %,
volume, 52-week high, moving average, stop level, holding window, filing, order book, P/E, profit margin, debt,
return on equity, surveillance list, upper circuit, short-term gains tax, Nifty 50, fixed deposit, and others.

## 4. Architecture

Same layers as v1 (plan section 5): pure domain rules, services, adapters, web.

| Piece | Where | What it does |
|---|---|---|
| Practice rules | `domain/practice.py` (pure) | Entry price, exit checks (stop, window, optional target, gap-down handling), position size, P&L after costs and tax, verdict thresholds, FD and index comparison. Fully unit tested |
| Storage | migration `0003`: `practice_trades`, `practice_daily` | Each trade: pick, rules version, entry date and price, quantity, stop, planned exit, exit date, price and reason, costs, tax, net P&L, status. Daily snapshot: portfolio value, Nifty value, FD value |
| Updating | `services/practice.py`, called by the existing **market-close** job | Opens trades for picks from stored opening prices, applies exits on that day's high and low, writes the daily snapshot. Idempotent and derived only from stored prices, so it can rebuild from any date without hindsight (picks made before this ships are included automatically) |
| Weekly email | `notify.weekly_practice()` plus a Friday 19:30 timer | Plain-language digest |
| Plain words | `web/presenters.py` | Turns report data into beginner sentences ("why it might rise", rupee risk on your amount). Unit tested, kept out of the domain rules |
| Glossary | `web/glossary.py` plus a template macro | One source for every definition. CSP-safe: no scripts needed |
| Charts | Server-generated inline SVG | No chart library, works with the strict security policy and on phones |
| Your amount | Setting `REFERENCE_INVESTMENT` (default Rs 40,000) | Used everywhere rupee examples are shown |

### Integrity safeguards

- Trades are append-only. A rules change creates a new rules version; it never rewrites history.
- Entry uses the next day's open, never the previous close. A gap down through the stop sells at the open, not at the
  stop price, which is what would really happen.
- Results always include costs and tax, and state the sample size next to every rate.

## 5. Delivery

| # | Milestone | Acceptance | Who |
|---|---|---|---|
| V1 | Practice engine | Unit tests for every rule, including gap-downs and window ends; rebuild from 12 Nov produces identical results twice | Claude |
| V2 | Practice page and weekly email | One-sentence answer, verdict, chart, rating bars, trade stories; email arrives Friday | Claude; you review the wording |
| V3 | Plain-language picks and reports | "In plain words" box, rupee risk, new labels, details collapsed | Claude; you review with a beginner if you can |
| V4 | Glossary, welcome cards, How it works page | Every listed term has a definition; first visit shows the welcome | Claude |
| V5 | Deploy and verify | Live on the server; tests pass; checked on phone and desktop | Claude |

## 6. Decisions (made 2026-11-12)

| # | Decision | Chosen |
|---|---|---|
| D1 | Practice amount | **Rs 40,000 per day, split equally across that day's picks.** Trades overlap (holds last up to 15 trading days), so the headline reports the total invested so far and the profit on it, for example: "Putting Rs 40,000 into each day's picks (Rs 1,20,000 invested so far), you'd be up Rs 3,700 after costs." Days with no picks invest nothing |
| D2 | Take profit at +10%? | **No.** Sell only at the safety (stop) level or when the holding window ends |
| D3 | Who will use the app? | **Only Pavan.** Single login stays; no multi-user work |

## 7. What this won't do

It won't make the picks more accurate. It makes them understandable and makes the evidence about whether they work
impossible to miss. The verdict will say "too early to tell" for the first month, and that is the correct answer.
