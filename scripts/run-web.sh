#!/usr/bin/env bash
# Launch the FastAPI web app.
#
# Binds all interfaces by default so other devices on the same wifi can reach
# it at http://<this-host-ip>:8000. Override HOST/PORT via env. The database
# defaults to SQLite (web/dev.db); set BUDGET_DATABASE_URL for Postgres.
#
# Prefers a dedicated .venv-web so it never disturbs the legacy cron's .venv.
# First-time setup:
#   python3 -m venv .venv-web
#   .venv-web/bin/pip install -r requirements.txt -r web/requirements.txt
set -euo pipefail
cd "$(dirname "$0")/.."

VENV="${VENV:-.venv-web}"
[ -x "$VENV/bin/uvicorn" ] || VENV=".venv"
if [ ! -x "$VENV/bin/uvicorn" ]; then
  echo "No uvicorn found in .venv-web or .venv. Create one and install deps:" >&2
  echo "  python3 -m venv .venv-web" >&2
  echo "  .venv-web/bin/pip install -r requirements.txt -r web/requirements.txt" >&2
  exit 1
fi

export PYTHONPATH="core${PYTHONPATH:+:$PYTHONPATH}"
echo "Serving web.app.main:app on ${HOST:-0.0.0.0}:${PORT:-8000} via $VENV" >&2
exec "$VENV/bin/uvicorn" web.app.main:app \
  --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
