#!/usr/bin/env bash
# One-command deploy for the web app on the always-on box (the Lenovo).
#
#   scripts/deploy-web.sh
#
# Pulls the latest code, then restarts uvicorn so the new code actually takes
# effect. This uvicorn runs plain (no --reload), so a bare `git pull` alone
# leaves the OLD code loaded in memory — the classic "new templates on disk +
# stale process" bug (e.g. jinja2 "'t' is undefined"). This script guarantees a
# fresh process.
#
# Runtime config (secrets, BUDGET_BASE_URL, …) is read from WEB_ENV_FILE
# (default ~/.config/budget-web.env), the same file run-web.sh and the @reboot
# cron entry use. A git `post-merge` hook restarts on every pull too; this script
# is the explicit path and disables that hook for its own pull to avoid a double
# restart.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
ENVF="${WEB_ENV_FILE:-$HOME/.config/budget-web.env}"
LOG="$REPO/web-app.log"
PORT="${PORT:-8000}"

echo "==> git pull --ff-only"
# Skip hooks here; this script owns the single authoritative restart below.
git -c core.hooksPath=/dev/null pull --ff-only

echo "==> restarting uvicorn on :$PORT"
pids_on_port() { ss -ltnp "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u; }
PIDS=$(pids_on_port)
[ -n "$PIDS" ] && kill $PIDS 2>/dev/null
for _ in $(seq 1 20); do ss -ltn | grep -q ":$PORT " || break; sleep 0.5; done
if ss -ltn | grep -q ":$PORT "; then kill -9 $(pids_on_port) 2>/dev/null; sleep 1; fi

[ -f "$ENVF" ] && { set -a; . "$ENVF"; set +a; }
echo "----- deploy restart $(date '+%F %T %z') -----" >> "$LOG"
setsid nohup /usr/bin/flock -n "/tmp/budget-web.lock" \
  .venv-web/bin/uvicorn web.app.main:app --host 0.0.0.0 --port "$PORT" \
  >> "$LOG" 2>&1 < /dev/null &

for _ in $(seq 1 20); do ss -ltn | grep -q ":$PORT " && break; sleep 0.5; done
sleep 2
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:$PORT/login" || echo 000)
echo "==> /login -> $code"
if [ "$code" != "200" ]; then
  echo "WARNING: /login returned $code — check $LOG" >&2
  tail -n 15 "$LOG" >&2
  exit 1
fi
echo "==> web app deployed and healthy at :$PORT"
