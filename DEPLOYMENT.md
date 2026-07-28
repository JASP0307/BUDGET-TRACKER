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
   `BUDGET_FERNET_KEY` is missing, under 32 chars, or still a dev default, if
   `BUDGET_BASE_URL` isn't https, or if outbound mail is unconfigured (see the
   next step). Generate each with `openssl rand -hex 32` (the Fernet key with
   the one-liner in `.env.example`). A failed boot prints every problem at once
   — read the container log.
2. **Configure outbound mail (Resend).** Without it `send_email` only *logs* the
   message, so registration says "check your email" while the confirmation link
   dies in the log — the app looks fine and every signup is lost. Add your domain
   in Resend, publish the DKIM/SPF records it gives you, wait for **verified**,
   then put the API key in `BUDGET_RESEND_TOKEN` and an address on that domain in
   `BUDGET_FROM_EMAIL`. Free tier is 3,000/month with a **100/day** cap.

   Outbound is *not* Postmark. Postmark gates sending behind a manual account
   approval — while it is pending, it accepts only recipients on your own
   domain, which looks like success when you test on yourself and drops every
   real user's mail (`ErrorCode 412`). That cost this app a day of lost signups.
   Postmark still handles **inbound** (next steps); the two are independent.

   Verify in Resend's dashboard, not just your inbox: a delivered-but-spam-
   foldered mail means DKIM/DMARC needs work.
3. **Real domain in the `Caddyfile`,** replacing `app.example.do`. Caddy gets the
   certificate automatically; the file also allowlists Postmark's webhook IPs and
   sets HSTS.
4. **Inbound mail — Postmark or Resend.** Both routes are mounted; pick one.
   - **Resend** (free, no plan requirement): add a receiving domain such as
     `in.<yourdomain>` and publish its MX records; create a webhook for the
     `email.received` event pointing at
     `https://<host>/webhooks/resend-inbound/<BUDGET_WEBHOOK_SECRET>`; put its
     `whsec_…` signing secret in `BUDGET_RESEND_WEBHOOK_SECRET`. Set
     `BUDGET_INBOUND_DOMAIN=in.<yourdomain>` and **clear**
     `BUDGET_POSTMARK_INBOUND_ADDRESS` so `format_inbound_address` hands users
     the new address. Without the signing secret the route answers 404 — it is
     never served unsigned. Resend keeps inbound content 30 days.
   - **Postmark** requires the **Pro** plan (~$16.50/mo) for inbound at all, and
     its default 45-day content retention needs the data-retention add-on turned
     down to 7 days — otherwise users' bank emails live on Postmark far longer
     than in our own database, contradicting what `/privacy` tells them.

   **Switching providers changes every user's forwarding address.** Existing
   Gmail filters keep sending to the old one, so run both in parallel and have
   users re-point before the old provider is torn down.
5. **Install the retention cron** on the VPS host:
   ```cron
   30 3 * * * cd /srv/app && .venv/bin/python scripts/purge_raw_emails.py >> purge.log 2>&1
   ```
   Bodies of successfully parsed mail are already dropped at ingestion; this
   sweeps confirmation links (7 days), unreadable-format bodies (30 days) and
   old RawEmail rows. Preview with `--dry-run`.
6. **Smoke-test the security behavior**, not just the happy path: sign up →
   verify → forward one real bank email → confirm the transaction appears *and*
   `raw_emails.html_body` is empty for it; reset the password and confirm a
   session open in another browser gets bounced to `/login`; delete a throwaway
   account and confirm no rows survive for that user id.

Never expose `docker-compose.lan.yml` — it runs plain HTTP with
`BUDGET_ENV=development`, so cookies aren't Secure and the secret guard is off.

### Category suggestions (local LLM, optional)

A cron sweep (`scripts/suggest_categories.py`) asks a **local Ollama model** to
propose a category for transactions stuck in «Otros / sin categoría»; the
dashboard shows them as "Sugerido: X" with accept/dismiss, and accepting also
creates the matching rule. Merchant names never leave the box — that's the
point of running the model locally.

> **Today the model does not run on the Lenovo.** It is served by the Nitro over
> Tailscale (`BUDGET_OLLAMA_URL=http://100.123.55.111:11434`,
> `BUDGET_OLLAMA_MODEL=qwen2.5:3b`) because a 3 GB laptop cannot hold a model
> good enough to be useful — see the sizing note below and the scored comparison
> in NOTES.md. The Lenovo still owns the app, the database and the cron; only
> inference is remote, and the sweep fails soft whenever the Nitro is asleep.
> On the Nitro, Ollama is bound to the **tailnet IP only** so it is never
> exposed on public Wi-Fi:
>
> ```sh
> # /etc/systemd/system/ollama.service.d/tailnet.conf
> [Service]
> Environment="OLLAMA_HOST=100.123.55.111:11434"
> ```
>
> The unit has `Restart=always`, so it retries until Tailscale is up. Local CLI
> use needs `export OLLAMA_HOST=100.123.55.111:11434` (added to `~/.bashrc`).
> Revisit when the server moves to hardware that can host the model itself.

1. **Install Ollama and pull the model** on the deploy host. With root, the
   official installer works (`curl -fsSL https://ollama.com/install.sh | sh` —
   installs a systemd service). On the Lenovo there is no passwordless sudo, so
   it's a **user-mode install** like cloudflared and gh:
   ```sh
   mkdir -p ~/.local/ollama-dist ~/.local/bin
   curl -fsSL https://ollama.com/download/ollama-linux-amd64.tgz \
     | tar -xz -C ~/.local/ollama-dist
   ln -sf ~/.local/ollama-dist/bin/ollama ~/.local/bin/ollama
   ollama serve >> ~/ollama.log 2>&1 &   # kept alive by an @reboot cron
   ollama pull qwen2.5:3b
   ```
   **Pick the model by measuring it on real merchants, not by size.** The
   scored comparison is in NOTES.md → "Which local model, measured"; the short
   version is that `qwen2.5:3b` (1.9 GB) beat `gemma4:e2b` (7.2 GB) 7/8 vs 5/8
   and was ~10× faster, and `qwen2.5:1.5b` managed only 4/8. Ollama's
   cloud-hosted models (`gemma4:cloud`) are **not usable here at all** — they
   ignore the `format` JSON schema and answer in prose.

   RAM is what rules a host out. On the Lenovo (2 cores, 3.3 GB, ~1.2 GB
   baseline) `qwen2.5:3b` leaves ~370 MB available and thrashes into swap: a
   *five-token* call took 23s and a full sweep 9m40s with timeouts. That is why
   inference moved to the Nitro. `qwen2.5:1.5b` does fit there (10–15s/call) and
   is installed as a dormant fallback, but at 4/8 it is not worth switching back
   to. A host with real headroom should try `qwen2.5:7b` (~5 GB).

   A model unloads after ~5 min idle, so **every** sweep pays a cold load — that
   is why `OLLAMA_TIMEOUT` is 150s. And if you ever do run the model on the
   Lenovo, keep its desktop session closed: Firefox + VS Code were holding
   ~2 GB, which alone made any model unusable.
2. **Env (optional):** defaults are `BUDGET_OLLAMA_URL=http://127.0.0.1:11434`
   and `BUDGET_OLLAMA_MODEL=qwen2.5:3b`. Setting `BUDGET_OLLAMA_URL=""`
   disables the sweep entirely.
3. **Install the cron** (same host pattern as the retention cron):
   ```cron
   */15 * * * * cd /srv/app && flock -n /tmp/suggest.lock .venv/bin/python scripts/suggest_categories.py >> suggest.log 2>&1
   ```
   Preview with `--dry-run`. The sweep caps itself at 50 transactions per run
   and makes one model call per distinct merchant.

The webhook never calls the model — an Ollama outage only means no new
suggestions until the next sweep. Suggestions are conservative by design (the
prompt prefers "no answer" over a wrong guess); dismissed suggestions are
remembered and not re-asked.

### The Cloudflare named tunnel (public access, since 2026-07-25)

The Lenovo is reachable from the internet at **https://cualtoapp.com** (and
`www.`) through a **named** Cloudflare tunnel — **not** through the Caddy stack,
so the HSTS header and Postmark IP allowlist in the `Caddyfile` do not apply
here. Cloudflare terminates TLS, which is why `BUDGET_ENV=production` and its
https-only rules are correct on this box.

- Tunnel `cualto`, id `8bebb7dc-67e2-452d-b65c-7ef515f96d07`.
- Config `~/.cloudflared/config.yml` (0600) routes both hostnames to
  `http://localhost:8000`, with a `http_status:404` catch-all so anything else
  never reaches the app. Validate with `cloudflared tunnel ingress validate`.
- Credentials `~/.cloudflared/<tunnel-id>.json` and `~/.cloudflared/cert.pem` —
  **not** in the repo, and **not** yet in `backup-secrets.sh`; losing them means
  recreating the tunnel and re-pointing DNS.
- Started by cron: `@reboot sleep 15 && cloudflared tunnel run cualto`, logging to
  `~/cloudflared-named.log`. Run it by hand the same way after killing it.
- No open ports on the box or the router; the tunnel dials out.

**Why this replaced the quick tunnel.** A quick tunnel gets a new random
`*.trycloudflare.com` hostname on every start, and `BUDGET_BASE_URL` is baked into
every verification and reset email — so a restart silently left the app mailing
links to a host that no longer resolved. `scripts/sync-tunnel-url.sh` papered over
that by rewriting the env file every five minutes; it was **deleted** with this
change. If you ever reintroduce a quick tunnel, note that script would fight a
named one: it starts a quick tunnel whenever it finds none and overwrites
`BUDGET_BASE_URL` with the random hostname.

**Still to do:** HSTS is not set for this hostname (Cloudflare does not add it by
default) — enable it in the dashboard under SSL/TLS → Edge Certificates.

### Onboarding screen recordings (`/static/clips/`)

`/setup` and `/help` each offer a short recording of the Gmail steps. The files
are **git-ignored on purpose** — the repo is public and video is permanent
weight in the history — so they do not arrive with `git pull` and have to be
copied to the box separately:

```
rsync -av web/app/static/clips/ lenovo:~/Documents/BUDGET-TRACKER/web/app/static/clips/
```

Names are fixed, `.mp4`, with an optional same-named `.jpg` poster beside each:
`overview` (the full walkthrough, `/help` only), `step1-forwarding`,
`step2-filter`, `step3-backfill`. Anything absent renders as nothing at all —
a missing clip is never an error, so the app is safe to deploy before any of
them exist.

Serve them from here rather than embedding a third-party player: the CSP this
deployment still owes itself would otherwise need a `frame-src` hole.

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
