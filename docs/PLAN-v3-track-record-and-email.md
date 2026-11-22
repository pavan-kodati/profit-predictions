# Plan v3: the app's own track record, picks by company size, and a premium email

Status: **draft for approval**. Date: 2026-11-13. Builds on [PLAN.md](PLAN.md) and
[PLAN-v2-practice-and-plain-language.md](PLAN-v2-practice-and-plain-language.md).
Email layouts to look at: [mockups/email-options.html](mockups/email-options.html).

## 1. What Pavan asked for

| # | Request | What it means for the product |
|---|---|---|
| R1 | The email is informative but hard to read | Redesign it; the "if you buy" numbers become a table, not a sentence |
| R2 | "Do the paper trading yourself" and show it | A page where the app is held to account for every pick it made |
| R3 | Say **why** a pick rose or fell: business, market, war | A reason next to every result, with sources, and "no clear reason found" when there isn't one |
| R4 | Track the stocks the app rejected too | Show whether saying no was right |
| R5 | Picks repeat from one day to the next | A stock is a new pick once; after that it is listed as "still running" |
| R6 | Large, mid and small companies are mixed together | Up to 3 picks in each size group |
| R7 | Share prices differ a lot (some cost over Rs 10,000) | Every pick shows its price and what Rs 10,000 buys |
| R8 | Rs 40,000 is not a limit; assume Rs 10,000 per pick | New pretend-money rule |
| R9 | Premium look, and standards in the UI and the code | Section 8 |

### Decisions already made (13 Nov)

| # | Decision | Chosen |
|---|---|---|
| D1 | Morning email layout | **B**: summary table first, then one card per pick |
| D2 | Practice portfolio page | Becomes **My practice portfolio**: Pavan's own pretend trades |
| D3 | Share price | Group by company size only; show the price on each pick; if one share costs more than Rs 10,000, pretend-buy 1 share and say so |
| D4 | Results email | Separate email, **weekly, Friday evening** |
| D5 | Rejected stocks | A second tab on the track record page (my recommendation, as asked) |

## 2. Pages after this change

| Menu item | What it shows | Change |
|---|---|---|
| Today's picks | New picks grouped by size, then picks still running | Regrouped |
| **App's track record** | Every pick the app made, the pretend result, and why. Tab 2: stocks it said no to | **New.** Takes over today's Practice portfolio and the old Track record statistics |
| **My practice portfolio** | Pretend trades Pavan adds himself | **Repurposed** |
| My holdings | Real shares Pavan owns | No change |
| How it works | Rules in plain words | Updated for the new rules |
| For checking: Back-test, System | Unchanged | Track record moves out of this group |

## 3. Picks: size groups, no repeats, price

**Size groups (R6).** The group comes from NSE's official index lists, published on the same NSE site the app
already downloads its stock list from (the first step of phase 1 is confirming the server can fetch them):

| Group | Source list | Plain description shown to the user |
|---|---|---|
| Large | Nifty 100 | The 100 biggest companies; steadier prices |
| Mid-size | Nifty Midcap 150 | Ranked 101 to 250; faster growth, bigger swings |
| Small | Everything else the app scans | Ranked 251 and below; riskiest |

Each group shows **up to 3** picks, best rating first. The bar does not move: a pick still needs a rating of 3.5 or
more, a confirmed reason and no safety problem. A group with nothing good enough says so. On some days that will be
all three groups, and that is the correct answer.

To give each group a fair chance, the daily shortlist changes from "top 20 overall" to "top 10 in each group".

**No repeats (R5).** A stock appears as a *new pick* once. While the app's pretend trade in it is open, it is listed
under "Still running" with its result so far. It can be picked again only after that trade closes **and** a new
confirmed event has happened since.

**Price (R7).** Every pick shows the share price and what Rs 10,000 buys ("2 shares, you pay Rs 6,798"). If one
share costs more than Rs 10,000 the pick says "1 share costs Rs 11,200, more than Rs 10,000".

## 4. App's track record (R2, R3, R4)

### Pretend-money rules, version 2 (R8)

1. **Rs 10,000 goes into every pick**, as whole shares. If one share costs more, 1 share is bought.
2. Buying price, selling rules, costs and tax are unchanged from plan v2.
3. Trades are never edited. Trades made under the old rule (each day's picks shared Rs 40,000) stay as recorded
   and are marked with the rule they used.

### What the page shows

1. **One sentence in rupees:** "12 picks so far. Rs 1,20,000 of pretend money is now worth Rs 1,23,400: up
   Rs 3,400 after costs and tax", with the same comparison against the Nifty and a fixed deposit as today.
2. **Waiting to buy:** picks published but not yet bought, so the page is never empty.
3. **Still holding** and **Finished**: one row per pick: picked on, bought at, now or sold at, result in rupees and
   percent, and **why it moved**.
4. Filter by size group.

### "Why it moved" (R3)

Computed by fixed rules, like the ratings, so it cannot be made up. Checked in this order:

| Shown as | Rule | Evidence shown |
|---|---|---|
| **The business** | A confirmed company event happened while it was held (results, order, filing) and the price moved the matching way | Link to the filing or news |
| **The whole market** | The Nifty moved 1.5% or more the same way over the same days, and that covers most of the move | Nifty change, plus what trusted newspapers said about the market on the biggest day (this is where a war or a policy shock shows up), with links |
| **Its sector** | Shares in the same industry moved together | Typical move of the sector's shares |
| **Excitement faded** | A "mostly excitement" pick that fell with no news | None needed |
| **No clear reason found** | None of the above | Said plainly |

AI may write the one-line summary, under the existing rule: every statement must quote a stored document, or it is
dropped. The reason is updated daily while a pick is held and frozen when it is sold.

Limit to be honest about: this shows what moved *with* the price, which is strong evidence of the cause but not
proof.

### Stocks the app said no to (R4)

Every stock that was researched and rated but not picked is followed for 10 market days from the day it was
rejected. No pretend money, just the price change and a verdict:

| Verdict | Rule |
|---|---|
| Right to skip | It fell, or did worse than the Nifty |
| Made little difference | Within 3% of the Nifty |
| Missed a gain | It beat the Nifty by more than 3% |

The tab opens with one sentence: "Of 40 stocks skipped, 27 were right to skip."

## 5. My practice portfolio (D2)

- A **Practise buying this** button on every pick and stock report. Pavan chooses the amount (Rs 10,000 by default).
- Bought at the next market open, same as the app's trades. Sold by the same safety level and holding time, or
  earlier with a **Sell now** button (sold at the next open).
- Same costs and tax. Kept completely separate from the app's record, so Pavan's choices never change the app's
  score.

## 6. Morning email (R1, D1)

Layout B from the mockups:

1. **Subject:** "3 new picks for Fri 13 Nov" or "No new picks today".
2. **Summary table:** new picks by size group: rating, shares for Rs 10,000, sell-if-below level, most you'd lose.
3. **Still running:** earlier picks with the result so far. One line each.
4. **One card per new pick:** rating, label, why it might go up, what could go wrong, sources, link to the report.
5. Alerts on real holdings stay at the top when there are any.

Evidence links (R1, "check for improvements"): official filings first, at most 3, a short title as the link, and
the date and source type in grey after it.

Built for email programs: table-based layout with inline styles, 600px wide, readable on a phone and in dark
mode, with a plain-text version.

## 7. Weekly results email (D4)

Sent Friday 19:30, replacing today's weekly practice email. Contents as in the third mockup: one sentence in
rupees, one row per pick with the result and why it moved, then the stocks the app said no to.

## 8. Standards (R9)

| Area | Standard |
|---|---|
| Look | shadcn/ui tokens and components only, one stylesheet, light and dark |
| Words | Plain language; every technical term has a tap-to-explain definition |
| Accessibility | WCAG AA contrast, keyboard use, works at phone width |
| Security | Strict content security policy unchanged: no inline styles or scripts on the site |
| Code structure | Rules in `domain` (pure, no database), orchestration in `services`, pages in `web`; new rules never go in templates |
| Code quality | Type hints checked by mypy, ruff lint, a test for every rule, a database migration for every schema change |
| Honesty | Numbers come from code, never from AI. Rules are versioned. Trades are append-only |
| Version control | Recommended: put the project under git (secrets stay out). Not a git repository today |

## 9. Build order

| Phase | Contents | Done when |
|---|---|---|
| 1 | Size groups, no-repeat rule, Rs 10,000 rule v2, migration | Tests pass; today's picks page shows three groups |
| 2 | App's track record page, why-it-moved, rejected tab | Demo data shows every reason type correctly |
| 3 | Morning email B and weekly results email | Both render correctly in Gmail on desktop and phone |
| 4 | My practice portfolio | A pretend trade can be added, followed and sold |
| 5 | Full test run, browser check, deploy | Live site and next morning's email verified |

## 10. Risks

| Risk | Handling |
|---|---|
| More stocks researched each day (30 instead of 20) may hit the free AI limit | Research is already queued and resumable; lowest-ranked candidates are rated on official data only |
| Small companies are easier to manipulate and harder to sell | Existing liquidity and watch-list checks stay; the group is labelled "riskiest" |
| "Why it moved" could sound more certain than it is | Fixed wording, sources shown, "no clear reason found" used freely |
| Up to 9 picks a day could tempt over-trading | The email says picks are options to consider, not a list to buy in full |
