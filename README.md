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

- **Cards tracked:** Popular VISA ISI \*1111 (credit), Popular Visa Débito
  Clásica \*2222 (debit), Qik \*3333 (credit).
- **Handled email types:** Popular Consumo, Popular Retiro (→ *Retiro Efectivo*),
  Qik purchase, Qik reversal (logged as a negative amount). Declined charges are
  ignored.
- **USD → DOP:** converted at a manual rate in `config.toml`.
- **Dedupe:** the Sheet's `message_id` column is the source of truth (the
  read-only Gmail scope can't label messages).

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
python -m budgettracker.main          # one polling pass
```

### Cron (home server / Pi) — every 15 minutes
```cron
*/15 * * * * cd /path/to/app && .venv/bin/python -m budgettracker.main >> tracker.log 2>&1
```

## Tests

```bash
pip install pytest
pytest
```

Parser tests run against fixtures derived from real notification emails
(`tests/fixtures/`); no credentials needed.

## Status

- ✅ Parsers + categorization (tested against real samples)
- ✅ Gmail fetch, Sheets append/dedupe, Telegram alerts (implemented; need
  credentials to run end-to-end)
- ⬜ First live run once Google Cloud + Telegram + Sheet are set up
