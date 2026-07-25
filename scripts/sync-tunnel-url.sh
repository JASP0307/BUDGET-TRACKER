#!/usr/bin/env bash
# Keep BUDGET_BASE_URL pointing at the live Cloudflare quick tunnel.
#
#   scripts/sync-tunnel-url.sh [--dry-run]
#
# WHY THIS EXISTS
# A quick tunnel (`cloudflared tunnel --url http://localhost:8000`) gets a NEW
# random *.trycloudflare.com hostname every time it starts. BUDGET_BASE_URL is
# baked into every verification and password-reset email, and get_settings()
# reads it from the process environment — so after a cloudflared restart the app
# keeps mailing links to a hostname that no longer resolves. Nothing errors; the
# links just quietly stop working, which is the worst kind of breakage.
#
# This script is the stopgap until a real domain + named tunnel exists (see
# TASKS.md). It:
#   1. makes sure cloudflared is running at all (it is not supervised — no
#      systemd unit — so a reboot or a crash otherwise leaves no tunnel),
#   2. asks cloudflared for its current hostname,
#   3. rewrites BUDGET_BASE_URL in the env file if it drifted,
#   4. restarts uvicorn so the new value is actually in its environment.
#
# Safe to run every few minutes: it does nothing when the URL already matches.
# A restart does not log anyone out — the session cookie is signed with
# BUDGET_SESSION_SECRET, which this script never touches.
#
# Install (crontab):
#   @reboot        sleep 30 && cd <repo> && scripts/sync-tunnel-url.sh >> tunnel.log 2>&1
#   */5 * * * *    cd <repo> && scripts/sync-tunnel-url.sh >> tunnel.log 2>&1
set -uo pipefail
# Explicit PATH: this runs from cron, where the inherited PATH is minimal and
# ss/pgrep/setsid/flock are easy to lose.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd "$(dirname "$0")/.."
REPO="$PWD"
ENVF="${WEB_ENV_FILE:-$HOME/.config/budget-web.env}"
LOG="$REPO/web-app.log"
PORT="${PORT:-8000}"
CLOUDFLARED="${CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
TUNNEL_LOG="${TUNNEL_LOG:-$HOME/cloudflared.log}"
DRY_RUN=""
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

say() { echo "$(date '+%F %T') $*"; }

# ---- 1. cloudflared must be running --------------------------------------
# Match the full command so we never adopt some unrelated cloudflared.
if ! pgrep -f "cloudflared tunnel --url http://localhost:$PORT" >/dev/null; then
  if [ -n "$DRY_RUN" ]; then
    say "would start cloudflared (not running)"
  elif [ ! -x "$CLOUDFLARED" ]; then
    say "ERROR: cloudflared not found at $CLOUDFLARED" >&2
    exit 1
  else
    say "cloudflared not running — starting it"
    setsid nohup "$CLOUDFLARED" tunnel --url "http://localhost:$PORT" \
      >> "$TUNNEL_LOG" 2>&1 < /dev/null &
    # A quick tunnel needs a few seconds to register and publish its hostname.
    for _ in $(seq 1 30); do
      pgrep -f "cloudflared tunnel --url http://localhost:$PORT" >/dev/null && break
      sleep 1
    done
    sleep 5
  fi
fi

# ---- 2. ask cloudflared for its current hostname -------------------------
# The metrics port is chosen at random per run, so discover it from the process
# rather than hardcoding. /quicktunnel returns {"hostname":"...trycloudflare.com"}.
hostname=""
pid=$(pgrep -f "cloudflared tunnel --url http://localhost:$PORT" | head -1)
if [ -n "$pid" ]; then
  for port in $(ss -ltnp 2>/dev/null | grep -oP "127\.0\.0\.1:\K[0-9]+(?=.*pid=$pid,)"); do
    hostname=$(curl -s --max-time 5 "http://127.0.0.1:$port/quicktunnel" \
      | grep -oE '[a-z0-9-]+\.trycloudflare\.com')
    [ -n "$hostname" ] && break
  done
fi
# Fallback: the startup banner in cloudflared's own log.
if [ -z "$hostname" ] && [ -f "$TUNNEL_LOG" ]; then
  hostname=$(grep -oE '[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | tail -1)
  [ -n "$hostname" ] && say "note: metrics unavailable, used $TUNNEL_LOG"
fi
if [ -z "$hostname" ]; then
  say "ERROR: could not determine the tunnel hostname" >&2
  exit 1
fi
live_url="https://$hostname"

# ---- 3. compare and rewrite ---------------------------------------------
current=$(grep -m1 '^BUDGET_BASE_URL=' "$ENVF" 2>/dev/null | cut -d= -f2-)
if [ "$current" = "$live_url" ]; then
  say "ok — BUDGET_BASE_URL already $live_url"
  exit 0
fi
say "tunnel URL drifted: '$current' -> '$live_url'"
if [ -n "$DRY_RUN" ]; then
  say "would update $ENVF and restart uvicorn"
  exit 0
fi
cp "$ENVF" "$ENVF.bak-tunnel-$(date +%F-%H%M%S)"
if grep -q '^BUDGET_BASE_URL=' "$ENVF"; then
  # The URL is [a-z0-9.-] only, so a plain | delimiter is safe here.
  sed -i "s|^BUDGET_BASE_URL=.*|BUDGET_BASE_URL=$live_url|" "$ENVF"
else
  printf 'BUDGET_BASE_URL=%s\n' "$live_url" >> "$ENVF"
fi

# ---- 4. restart uvicorn so it picks up the new environment ---------------
# Mirrors the restart in deploy-web.sh; kept separate so this never git-pulls.
say "restarting uvicorn on :$PORT"
pids_on_port() { ss -ltnp "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u; }
PIDS=$(pids_on_port)
[ -n "$PIDS" ] && kill $PIDS 2>/dev/null
for _ in $(seq 1 20); do ss -ltn | grep -q ":$PORT " || break; sleep 0.5; done
if ss -ltn | grep -q ":$PORT "; then kill -9 $(pids_on_port) 2>/dev/null; sleep 1; fi

[ -f "$ENVF" ] && { set -a; . "$ENVF"; set +a; }
echo "----- tunnel-sync restart $(date '+%F %T %z') -----" >> "$LOG"
setsid nohup /usr/bin/flock -n "/tmp/budget-web.lock" \
  .venv-web/bin/uvicorn web.app.main:app --host 0.0.0.0 --port "$PORT" \
  >> "$LOG" 2>&1 < /dev/null &

for _ in $(seq 1 20); do ss -ltn | grep -q ":$PORT " && break; sleep 0.5; done
sleep 2
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:$PORT/login" || echo 000)
say "/login -> $code"
if [ "$code" != "200" ]; then
  say "WARNING: /login returned $code — check $LOG" >&2
  tail -n 15 "$LOG" >&2
  exit 1
fi
say "synced to $live_url and healthy"
