# Deployment

Current deployment of the budget tracker. Setup details for a fresh machine are
in `README.md`; this file records where it actually runs today.

## Where it runs
- **Host:** old Lenovo laptop (Linux), on AC power. To migrate to a Raspberry Pi
  later, see "Migration" below.
- **Path:** `/home/jabner/Documents/SystemTree/SystemTree/20_Projects/budget-tracker/app`
- **Schedule:** cron, every 15 min, via `scripts/run.sh` (flock-guarded so runs
  can't overlap). Logs to `tracker.log`.
- **OAuth:** Google app is in **Production**, so the token does not expire every
  7 days.

## Crontab
```cron
*/15 * * * * /usr/bin/flock -n /tmp/budget-tracker.lock /home/jabner/Documents/SystemTree/SystemTree/20_Projects/budget-tracker/app/scripts/run.sh >> /home/jabner/Documents/SystemTree/SystemTree/20_Projects/budget-tracker/app/tracker.log 2>&1
0 20 * * * /usr/bin/flock -w 300 /tmp/budget-tracker.lock /home/jabner/Documents/SystemTree/SystemTree/20_Projects/budget-tracker/app/scripts/run.sh --heartbeat >> /home/jabner/Documents/SystemTree/SystemTree/20_Projects/budget-tracker/app/tracker.log 2>&1
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
- **Run once now:** `.venv/bin/python -m budgettracker.main`
- **See what would be logged (no writes):** `... --dry-run`
- **Backfill the current month (after downtime):** `... --backfill`
- **Send the heartbeat now:** `... --heartbeat` (preview without sending:
  `... --heartbeat --dry-run`)
- **Change budget/rules/FX rate:** edit `config.toml` (takes effect next run).
- **New month:** month-to-date formulas roll over automatically (they filter on
  the current month); historical rows stay in the sheet.

## Migration to Raspberry Pi (later)
1. Clone the GitHub repo on the Pi.
2. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. Copy `config.toml`, `credentials.json`, `token.json` from the laptop (scp).
4. Install the same crontab line with the Pi's path.
5. Confirm a run, then retire the laptop's crontab entry.
