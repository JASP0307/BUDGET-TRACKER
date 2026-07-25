# Deployment

Current deployment of the budget tracker. Setup details for a fresh machine are
in `README.md`; this file records where it actually runs today.

For the v2 web app's production checklist (secrets, retention, webhook
allowlist), see "Web app (v2) — going internet-facing" at the bottom.

## Where it runs
- **Host:** old Lenovo laptop (Linux), on AC power. To migrate to a Raspberry Pi
  later, see "Migration" below.
- **Path:** `/home/jabner/Documents/BUDGET-TRACKER` (the dev copy on the main
  machine lives inside the SystemTree vault at
  `.../SystemTree/20_Projects/budget-tracker/app`; deploy = push here, pull there)
- **Schedule:** cron, every 15 min, via `scripts/run.sh` (flock-guarded so runs
  can't overlap). Logs to `tracker.log`.
- **OAuth:** Google app is in **Production**, so the token does not expire every
  7 days.

## Crontab
```cron
*/15 * * * * /usr/bin/flock -n /tmp/budget-tracker.lock /home/jabner/Documents/BUDGET-TRACKER/scripts/run.sh >> /home/jabner/Documents/BUDGET-TRACKER/tracker.log 2>&1
0 20 * * * /usr/bin/flock -w 300 /tmp/budget-tracker.lock /home/jabner/Documents/BUDGET-TRACKER/scripts/run.sh --heartbeat >> /home/jabner/Documents/BUDGET-TRACKER/tracker.log 2>&1
```
The second line is the daily 8 PM heartbeat. It shares the poller's lock but
waits (up to 5 min) instead of skipping, so it is never silently dropped by an
in-flight poll — a missing heartbeat should always mean the machine is down.
- Show it: `crontab -l`
- Remove it: `crontab -e` and delete the line (or `crontab -r` to clear all).

## Secrets (git-ignored, machine-local — never commit or push)
- `config.toml` — FX rate, spreadsheet id, Telegram token/chat id, rules, budget
- `credentials.json` — Google OAuth client
- `token.json` — cached OAuth token (Gmail read-only + Sheets)

## Laptop must stay awake
cron does **not** run while suspended. On the Lenovo, sleep is disabled via
`/etc/systemd/logind.conf` (`HandleLidSwitch=ignore`) and masked sleep targets —
see README "Running the service on a laptop". A Pi never sleeps, so this section
won't apply after migration.

## Health checks
- `crontab -l` — both entries present (15-min poller + daily heartbeat).
- `tail -f tracker.log` — timestamped run every 15 min; each line ends with
  "logged N new transaction(s)".
- Swipe a card → Telegram alert should arrive within 15 min.
- **Daily heartbeat at 8 PM** ("✅ Tracker vivo — N txns hoy…"). No heartbeat
  by ~8:10 PM means the machine or cron is down — check the laptop.

## Common operations
- **Run once now:** `scripts/run.sh` (or `PYTHONPATH=core:legacy .venv/bin/python -m budgettracker.main`)
- **See what would be logged (no writes):** `... --dry-run`
- **Backfill the current month (after downtime):** `... --backfill`
- **Send the heartbeat now:** `... --heartbeat` (preview without sending:
  `... --heartbeat --dry-run`)
- **Change budget/rules/FX rate:** edit `config.toml` (takes effect next run).
- **New month:** month-to-date formulas roll over automatically (they filter on
  the current month); historical rows stay in the sheet.

## Web app (v2) — going internet-facing

The multi-user web app is a separate deployment (`docker-compose.yml` → Postgres
+ app + Caddy). Before it serves anyone but you:

1. **Set `BUDGET_ENV=production` and fill every secret in `.env`.** The app
   refuses to boot in production if `BUDGET_SESSION_SECRET`,
   `BUDGET_WEBHOOK_SECRET`, `BUDGET_TELEGRAM_WEBHOOK_SECRET` or
   `BUDGET_FERNET_KEY` is missing, under 32 chars, or still a dev default, or if
   `BUDGET_BASE_URL` isn't https. Generate each with `openssl rand -hex 32`
   (the Fernet key with the one-liner in `.env.example`). A failed boot prints
   every problem at once — read the container log.
2. **Real domain in the `Caddyfile`,** replacing `app.example.do`. Caddy gets the
   certificate automatically; the file also allowlists Postmark's webhook IPs and
   sets HSTS.
3. **Turn Postmark's inbound retention down to 7 days** (data-retention add-on).
   Postmark keeps message content 45 days by default, so without this, forwarded
   bank emails live on Postmark much longer than in our own database — which
   contradicts what `/privacy` tells users.
4. **Install the retention cron** on the VPS host:
   ```cron
   30 3 * * * cd /srv/app && .venv/bin/python scripts/purge_raw_emails.py >> purge.log 2>&1
   ```
   Bodies of successfully parsed mail are already dropped at ingestion; this
   sweeps confirmation links (7 days), unreadable-format bodies (30 days) and
   old RawEmail rows. Preview with `--dry-run`.
5. **Smoke-test the security behavior**, not just the happy path: sign up →
   verify → forward one real bank email → confirm the transaction appears *and*
   `raw_emails.html_body` is empty for it; reset the password and confirm a
   session open in another browser gets bounced to `/login`; delete a throwaway
   account and confirm no rows survive for that user id.

Never expose `docker-compose.lan.yml` — it runs plain HTTP with
`BUDGET_ENV=development`, so cookies aren't Secure and the secret guard is off.

### One-off migration for an existing database (2026-07-25)

`main.py` still uses `create_all`, which creates missing *tables* but never adds
a column to a table that already exists. The `users.session_version` column
added by the session-invalidation change therefore has to be applied by hand to
any database created before it. Without it every page 500s on
`SELECT users.session_version`. A fresh database needs nothing.

Apply it **before** deploying the new code: the column is invisible to the old
code, so there is no downtime window in that order (the reverse order breaks the
app until the ALTER lands).

This ordering is not optional on the Lenovo. A git `post-merge` hook restarts
uvicorn on every pull, so `git pull` alone is enough to bring the new code up —
if the column isn't there yet, the app restarts broken.

```sql
ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0;
```

**The live LAN trial (Lenovo)** runs uvicorn + `.venv-web` against SQLite at
`~/Documents/BUDGET-TRACKER/web/dev.db` — not Docker, and there is no `sqlite3`
CLI on that box, so use its own Python:

```bash
ssh lenovo
cd ~/Documents/BUDGET-TRACKER
cp web/dev.db "web/dev.db.bak-$(date +%F)"          # real user data — back up first
.venv-web/bin/python3.14 - <<'PY'
import sqlite3
db = sqlite3.connect("web/dev.db")
db.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
db.commit()
print([r[1] for r in db.execute("PRAGMA table_info(users)")])
PY
```

**A Postgres deployment** (the VPS compose stack) instead:

```bash
docker compose exec db psql -U budget -d budget -c \
  "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0;"
```

Then deploy normally with `scripts/deploy-web.sh` (pulls, restarts uvicorn, and
health-checks `/login` — don't hand-roll the restart).

Everyone is logged out once after the deploy: existing cookies carry no
`session_version`, so they read as stale. That is the feature working, not a
bug — users just sign in again.

Leave `BUDGET_ENV` unset in `~/.config/budget-web.env` on the Lenovo. The LAN
trial is plain HTTP, so `production` there would (correctly) refuse to boot for
lack of an https `BUDGET_BASE_URL`.

This is exactly the class of problem Alembic exists to prevent — see TASKS.md.

## Migration to Raspberry Pi (later)
1. Clone the GitHub repo on the Pi.
2. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. Copy `config.toml`, `credentials.json`, `token.json` from the laptop (scp).
4. Install the same crontab line with the Pi's path.
5. Confirm a run, then retire the laptop's crontab entry.
