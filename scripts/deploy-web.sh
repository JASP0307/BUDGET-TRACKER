#!/usr/bin/env bash
# One-command deploy for the web app on the always-on box (the Lenovo).
#
#   scripts/deploy-web.sh [--no-pull]
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
#
# --no-pull restarts without pulling. The post-merge hook uses it and does
# nothing else: the hook used to carry a *second* copy of the restart logic, so
# when the stop sequence learned to wait for the lock (below) the hook kept the
# old port-only wait and a bare `git pull` could still take the site down. One
# implementation, two entry points. Install the hook with scripts/install-hooks.sh.
#
# Stopping waits for the *lock* as well as the port. Those clear at different
# moments: the port is released by uvicorn, the lock by the `flock` parent that
# outlives it by a beat. On 2026-07-31 a deploy killed the old process, saw the
# port free, and started the new one while the old flock still held the lock —
# `flock -n` then failed with exit 1 and NO output, so the site went down with
# an empty log and a health check that only said "000". Waiting on both, and
# naming the two ways a start can fail, is the fix.
set -uo pipefail

PULL=true
case "${1:-}" in
  --no-pull) PULL=false ;;
  "") ;;
  *) echo "usage: $(basename "$0") [--no-pull]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."
REPO="$PWD"
ENVF="${WEB_ENV_FILE:-$HOME/.config/budget-web.env}"
LOG="$REPO/web-app.log"
PORT="${PORT:-8000}"
LOCK="${WEB_LOCK:-/tmp/budget-web.lock}"
# How long to wait for the old instance to let go, and for the new one to bind.
# Overridable so the failure paths can be exercised without a 20s wait.
STOP_TRIES="${STOP_TRIES:-40}"   # ×0.5s = 20s
START_TRIES="${START_TRIES:-40}" # ×0.5s = 20s

port_busy() { ss -ltn | grep -q ":$PORT "; }
pids_on_port() { ss -ltnp "sport = :$PORT" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u; }
# Succeeds only if nothing holds the lock: takes it, runs true, releases it.
lock_free() { /usr/bin/flock -n "$LOCK" true 2>/dev/null; }

if [ "$PULL" = true ]; then
  echo "==> git pull --ff-only"
  # Skip hooks here; this script owns the single authoritative restart below.
  git -c core.hooksPath=/dev/null pull --ff-only
fi

echo "==> restarting uvicorn on :$PORT"
PIDS=$(pids_on_port)
[ -n "$PIDS" ] && kill $PIDS 2>/dev/null

stopped=false
for _ in $(seq 1 "$STOP_TRIES"); do
  if ! port_busy && lock_free; then stopped=true; break; fi
  sleep 0.5
done

if [ "$stopped" != true ]; then
  # SIGKILL the port holder; the flock parent exits once its child is gone.
  kill -9 $(pids_on_port) 2>/dev/null
  for _ in $(seq 1 10); do
    if ! port_busy && lock_free; then stopped=true; break; fi
    sleep 0.5
  done
fi

if [ "$stopped" != true ]; then
  echo "ERROR: port :$PORT or $LOCK still held after SIGKILL — not starting a" >&2
  echo "       second instance. Check for a stray uvicorn (ps aux | grep uvicorn)." >&2
  exit 1
fi

[ -f "$ENVF" ] && { set -a; . "$ENVF"; set +a; }
echo "----- deploy restart $(date '+%F %T %z') -----" >> "$LOG"
setsid nohup /usr/bin/flock -n "$LOCK" \
  .venv-web/bin/uvicorn web.app.main:app --host 0.0.0.0 --port "$PORT" \
  >> "$LOG" 2>&1 < /dev/null &

for _ in $(seq 1 "$START_TRIES"); do port_busy && break; sleep 0.5; done

if ! port_busy; then
  # Two different failures, and the log looks the same (empty) for one of them.
  if ! lock_free; then
    echo "ERROR: could not start — something else holds $LOCK." >&2
    echo "       flock exits silently when the lock is taken, so $LOG has nothing." >&2
  else
    echo "ERROR: uvicorn exited during startup — see the traceback below." >&2
  fi
  tail -n 20 "$LOG" >&2
  exit 1
fi

sleep 2
# curl already prints 000 on a connection failure; a `|| echo 000` here used to
# concatenate a second one and report the memorable nonsense "/login -> 000000".
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:$PORT/login" 2>/dev/null)
code="${code:-000}"
echo "==> /login -> $code"
if [ "$code" != "200" ]; then
  echo "WARNING: /login returned $code — check $LOG" >&2
  tail -n 15 "$LOG" >&2
  exit 1
fi
echo "==> web app deployed and healthy at :$PORT"
