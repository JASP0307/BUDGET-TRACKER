# Budget Tracker

Parses Dominican bank card-notification emails (Banco Popular + Qik) from Gmail,
logs each transaction into a Google Sheet by budget category, and sends a
Telegram alert with the remaining budget after every charge.

Design notes, category model, and email-format reference live in the parent
folder's `NOTES.md` (kept in the personal-assistant vault, separate from this
repo).

## How it works

```
Gmail (read-only) ──▶ parse ──▶ categorize ──▶ Google Sheet row ──▶ Telegram alert
        ▲                                                                  │
        └────────────── cron on a home server / Pi ◀───────────────────────┘
```

- **Cards tracked:** one credit and one debit card from Banco Popular, plus a Qik
  credit card. Each deployment names its own in `config.toml` (`[cards.labels]`).
- **Handled email types:** Popular Consumo, Popular Retiro (→ *Retiro Efectivo*),
  Qik purchase, Qik reversal (logged as a negative amount). Declined charges are
  ignored.
- **USD → DOP:** converted at a manual rate in `config.toml`.
- **Dedupe:** the Sheet's `message_id` column is the source of truth (the
  read-only Gmail scope can't label messages).

## Layout

Monorepo, staged for the multi-user web version:

```
core/budgetcore/   # pure domain logic: parsers, categorize, dedupe, messages
core/tests/        # fixture-driven tests for the core (no credentials needed)
legacy/budgettracker/  # the single-user cron pipeline (Gmail/Sheets/Telegram I/O)
legacy/tests/
legacy/migrate_to_db.py  # one-off Sheet+config -> web DB import (idempotent)
web/app/           # FastAPI web app (Phase 1: single-tenant MVP)
web/tests/
scripts/run.sh     # cron entry point; sets PYTHONPATH=core:legacy
```

`budgetcore` has no I/O and depends only on `beautifulsoup4`; everything
Google/Telegram-specific stays in `legacy/budgettracker`.

## Web app (Phase 1 — single-tenant MVP)

Ingestion is inbound email (Postmark webhook at
`/webhooks/postmark-inbound/<secret>`) instead of Gmail OAuth: bank
notifications are auto-forwarded to a per-user `u_<token>@<inbound-domain>`
address. Pipeline: store raw email → route by token → Gmail-confirmation /
spoofing checks → `budgetcore` parse/categorize → dedupe (same signature as
the Sheet pipeline) → Postgres → Telegram alert. Dashboard: budget vs. actual
per category, recent transactions with one-click recategorize, rule and
budget editing.

```bash
pip install -r web/requirements.txt
PYTHONPATH=core:legacy:. python legacy/migrate_to_db.py   # import Sheet data (once)
PYTHONPATH=core:. uvicorn web.app.main:app --reload       # http://127.0.0.1:8000
```

Dev uses SQLite (`web/dev.db`, git-ignored); production uses the
`docker-compose.yml` stack (Postgres + app + Caddy) configured via `.env`
(see `.env.example`). Schema is `create_all` for now — Alembic arrives when
the schema stabilizes (pre-multi-user).

## Setup

### 1. Python
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Google Cloud (OAuth for Gmail + Sheets)
1. Create a project at <https://console.cloud.google.com>.
2. Enable the **Gmail API** and the **Google Sheets API**.
3. Configure the OAuth consent screen (External, add your Gmail as a test user).
4. Create an **OAuth client ID** of type **Desktop app**; download it as
   `credentials.json` into this folder.
5. First run opens a browser for consent and writes `token.json` (both are
   git-ignored).

### 3. Google Sheet
- Create a spreadsheet with two tabs:
  - **Transacciones** — headers: `message_id, date, card, type, merchant,
    category, amount_dop, original, currency`.
  - **Presupuesto** — the category/amount table from `NOTES.md`.
- Copy its ID (from the URL) into `config.toml`.

### 4. Telegram
1. Message **@BotFather**, `/newbot`, copy the token.
2. Send your new bot any message, then get your chat id from
   `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. Put the token + chat id in `config.toml`.

### 5. Config
```bash
cp config.example.toml config.toml   # then fill in the real values
```

## Run

```bash
export PYTHONPATH=core:legacy            # or just use scripts/run.sh
python -m budgettracker.main             # one polling pass
python -m budgettracker.main --dry-run   # fetch + parse + print, no writes
python -m budgettracker.main --backfill  # log this month's past txns (no Telegram)
```

### Cron — every 15 minutes
`scripts/run.sh` cd's into the repo, runs from the venv, and prints a timestamped
header. `flock` stops a slow run from overlapping the next one.

```cron
*/15 * * * * /usr/bin/flock -n /tmp/budget-tracker.lock /home/jabner/Documents/SystemTree/SystemTree/20_Projects/budget-tracker/app/scripts/run.sh >> /home/jabner/Documents/SystemTree/SystemTree/20_Projects/budget-tracker/app/tracker.log 2>&1
```

Install with `crontab -e` (or `crontab -l | { cat; echo "<line>"; } | crontab -`).

### Running the service on a laptop
cron does **not** fire while the machine is suspended, so a laptop must be kept
awake:
- Ignore the lid switch — in `/etc/systemd/logind.conf` set
  `HandleLidSwitch=ignore` (and `HandleLidSwitchExternalPower=ignore`), then
  `sudo systemctl restart systemd-logind`.
- Disable idle sleep — either in the desktop's power settings, or aggressively:
  `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`.
- Keep it on AC power.

Missed runs (machine off) self-heal on the next run because `fetch_window` looks
back 2 days; a longer outage needs a one-off `--backfill`.

## Tests

```bash
pip install pytest
pytest
```

Parser tests run against fixtures derived from real notification emails
(`core/tests/fixtures/`); no credentials needed.

## Status

- ✅ Parsers + categorization (tested against real emails)
- ✅ Gmail fetch, Sheets append + dedupe, Telegram alerts — live
- ✅ Duplicate-notification guard; backfill mode
- ✅ Running end-to-end (Google Cloud OAuth in Production, Sheet + Telegram set up)
- ⬜ Cron job installed on the always-on machine
