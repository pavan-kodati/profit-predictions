# Stock Research Assistant (profit-predictions)

A personal research assistant for the Indian stock market (Python 3.12, FastAPI, SQLite, Docker, hosted on one AWS
EC2 server that starts on weekdays and switches itself off when idle). It emails up to 3 rated picks per company-size
group each trading morning, keeps a pretend-money track record, and reviews the owner's real holdings. It never
places orders.

**Before doing any work, read `docs/APPLICATION-GUIDE.md`.** It describes the whole application end to end: how picks
are decided, what runs when, the website, emails, code layout, database, settings, AWS hosting and schedule, status
and open items, and the working rules. For server operations also read `docs/RUNBOOK.md`.

Rules that matter most (full list in section 16 of the guide):

- Never print, paste or commit secrets. `.env` and `.env.server` contain real keys: do not read them unless asked.
- AWS: always use `--profile pavan-personal` (account 542885031953). The default profile is a work account: never touch it. Ask before changing the firewall.
- The rating, safety price, pretend trades and "why it moved" are computed by code, never by the AI.
- Rules are versioned and trades are never edited. Every database change needs a migration; every rule needs a test.
- Rules live in `src/stockresearch/domain` (pure), jobs in `services`, pages in `web`. No rules in templates.
- Do not overwrite the server database with the laptop copy in `data/app.db` (an old development copy).
- Run `ruff check src tests`, `mypy` and `pytest` before deploying. Update `docs/APPLICATION-GUIDE.md` when behaviour changes.
