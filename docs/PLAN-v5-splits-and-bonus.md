# Plan v5: correct prices for splits and bonus issues

| | |
|---|---|
| **Status** | Approved and built 22 Nov 2026 (correction notes chosen for section 9). Not yet deployed |
| **Fixes** | Known data limit in `APPLICATION-GUIDE.md` section 14, item 7: "NSE's prices are not adjusted for splits and bonus issues, so a recent split can show a false fall" |

## 1. The problem in plain words

When a company splits its shares 1 into 5, the price drops to a fifth overnight, but nobody lost money: they hold 5 times
as many shares. The app stores NSE's official prices exactly as traded, so it sees an 80% crash. Today that causes:

| Where | What goes wrong |
|---|---|
| Pretend trades (`services/practice.py`, `domain/practice.find_exit`) | The next day's low is far below the safety price, so the trade is **sold at a fake loss**. The track record is wrong, and trades are never edited afterwards |
| Your holdings and exit alerts (`services/holdings.py`, `domain/exits.py`) | False "stop price hit" and "below 200-day average" alerts; holding health can show "Weakening" |
| Price and volume score, evening screen (`domain/technicals.py`, `scoring.py`, `screening.py`) | 50/200-day averages, 1-month gain and distance from the 1-year high are nonsense for up to a year. Volume looks 5 times normal, which can push a stock into the shortlist |
| Picks still running, stocks the app said no to (`services/tracker.py`, `rejections.py`) | Their stored reference price (`ref_close`) is compared with post-split prices: a fake fall, "Right to skip" given wrongly |
| "Why it moved", back-test, report price chart | Fake moves, fake back-test losses, a cliff in the chart |

A second, quieter problem: the two price sources disagree. Yahoo Finance prices (used for the first history load and when
NSE is unreachable) are already split-adjusted **as of the day they were downloaded**; NSE rows are raw. So adjusting
everything blindly would adjust Yahoo rows twice.

## 2. The approach

**Keep the stored prices exactly as received, and adjust when reading.** Nothing already stored is rewritten. A new table
records each split, bonus or consolidation with the factor it applies, and every reader asks for prices "as of" a chosen day.

- **Technicals, screen, scores, chart, back-test, rejections, "why it moved":** prices as of the latest day (the normal view).
- **A pretend trade:** prices as of *its own entry day*, so the stored entry price, safety price and quantity stay valid and are
  never edited. Settling with the old quantity and old-basis prices gives exactly the right rupee result.
- **Stored reference prices** (`picks.ref_close`, `rejections.ref_close`, `pick_outcomes.entry_price`): compared in the basis of the day they were recorded.
- **Your holdings:** average price and stop compared in the basis of the day the holding was entered (`created_at`); the
  holding page notes "adjusted for a 1:5 split on 3 Dec".

Factor rule: for an action with ex-date E and factor F (5 for a 1-into-5 split, 2 for a 1:1 bonus, 0.1 for a 10-into-1
consolidation), a price dated before E and expressed in pre-E terms is divided by F to express it after E; volume is multiplied
by F. Delivery % is unchanged.

## 3. Where the data comes from

1. **Primary (official tier): NSE's corporate-actions list** (`/api/corporates-corporateActions`). Fetched by the 19:00
   `market-close` job for the past 10 days and the next 30. The text is parsed by fixed rules, for example:
   - "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share" → factor 5
   - "Bonus 1:2" (1 new share for every 2 held) → factor 1.5
   - "Consolidation of shares ... Rs 1 to Rs 10" → factor 0.1
2. **Cross-check: NSE's own previous close.** The daily NSE price file already contains `PREV_CLOSE`, which NSE adjusts on the
   ex-date. If our stored close for the previous day differs from it by more than 2%, something changed the share count. If a
   parsed action explains it, good. If not, the stock gets a **"price history unreliable"** flag: it is held out of picks and the
   evening screen that day, its report says why, and the System page lists it. This also catches demergers and other actions the
   parser does not understand.
3. Text the parser cannot read is stored, never guessed: the stock gets the same flag until the cross-check or a manual entry resolves it.
4. **Manual entry** for corrections: `stockresearch corporate-action add SYMBOL EX_DATE FACTOR "note"`.

Rights issues and ordinary dividends are **not** adjusted (small effect; NSE does not adjust its previous close for normal dividends).

## 4. Changes, by layer

| Layer | Change |
|---|---|
| `domain/corporate_actions.py` (new, pure) | Parse NSE action text to a factor; compute the combined factor between two dates; adjust a price frame to a chosen basis day; the previous-close cross-check |
| `db/models.py` + migration `0005` | New table `corporate_actions` (symbol, ex_date, factor, kind, source text, source = nse/manual, created_at). New column `daily_prices.basis_date`: the day the row's prices are expressed in. NSE rows: their own date. Yahoo rows: the day they were downloaded |
| `adapters/nse.py`, `ports.py` | `corporate_actions(start, end)`; the price file also returns `PREV_CLOSE` |
| `services/repo.py` | `load_bars(..., basis=None)` applies the adjustment (default basis = latest day). Single place, so every reader benefits |
| `services/market.py` | New stage "Splits and bonus issues" before prices; the cross-check after prices; flagged stocks excluded from the screen |
| `services/practice.py`, `holdings.py`, `tracker.py`, `rejections.py`, `reasons.py` | Ask for prices in the basis that matches the stored prices they compare against |
| `domain/gates.py` | New gate: "price history unreliable (unexplained jump in share count)" |
| Report page, holding page | A line such as "Prices adjusted for a 1:5 split on 3 Dec 2026" |
| `cli.py` | `corporate-action add` and `corporate-action check` (see section 5) |

Existing Yahoo rows get `basis_date` = the later of their own date and 12 Nov 2026 (when the history was first loaded).
That is right for nearly all rows; any action between 12 Nov and the first run of this feature is caught by `corporate-action check`.

## 5. What happens to trades already made

`stockresearch corporate-action check` lists every pretend trade, practice trade, holding, pick and rejection whose dates span
a split or bonus, with what was affected. Run once on the server after deploying.

Trades are append-only, so a trade already closed at a fake loss **is not changed** (see the open question below). Open
trades are simply valued correctly from then on, because they are now read in their own basis.

## 6. Rules version

The buy and sell rules do not change, only the correctness of the prices fed to them, so `RULES_VERSION` stays 2. The
"price history unreliable" gate is a new rule and gets its own tests. `APPLICATION-GUIDE.md` sections 3, 9, 11 and 14 are updated.

## 7. Tests

- Parser: each NSE text form above, plus unreadable text, giving the right factor or "unknown".
- Adjustment: a 1:5 split in the middle of a frame gives a smooth series; volume scaled; two actions combine; basis before, on and after the ex-date.
- Mixed sources: a Yahoo row downloaded after the split is not adjusted twice.
- Pretend trade across a split is **not** stopped out, and settles to the right rupee amount.
- Holding across a bonus raises no false alert.
- Cross-check: unexplained 50% jump flags the stock and keeps it out of picks; a parsed action clears it.
- Rejection and tracker comparisons across a split.
- Adapter parsing of a saved NSE corporate-actions reply.

`ruff`, `mypy`, `pytest` (75% coverage) before deploying, then deploy as in `RUNBOOK.md`.

## 8. Risks and limits

- NSE's corporate-actions endpoint may be blocked from the server like other NSE pages. Then the cross-check still protects
  picks (flag instead of correct), and `feasibility` reports it. To confirm on the server before relying on it.
- The NSE text format is not documented and can change; unknown text flags rather than guesses.
- Demergers are flagged, not corrected.

## 9. Decision: correction notes

Pavan chose correction notes (22 Nov 2026). A finished trade that spanned an action recorded only after it ended gets a
row in `trade_corrections`; the trade is untouched, the track record shows the note and leaves the trade out of every total.

## 10. Built differently from this draft

- An unexplained jump in NSE's previous close is **corrected** with the factor NSE implies (not only flagged), because that
  figure is NSE's own adjustment. The stock is still kept out of picks for 14 days and an alert is raised.
- Finished rejections spanning a late-recorded action are re-judged on adjusted prices (they are notes, not trades).
- Holdings use the day the holding was entered (`created_at`) as the share-unit day for its quantity, average and stop.
