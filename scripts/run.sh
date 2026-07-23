#!/usr/bin/env bash
# Cron entry point: run one polling pass from the repo's own venv.
# Logs a timestamped header so tracker.log is easy to scan.
set -euo pipefail

cd "$(dirname "$0")/.."
echo "----- $(date '+%Y-%m-%d %H:%M:%S %z') -----"
# Monorepo layout: pure domain logic in core/, the cron pipeline in legacy/.
export PYTHONPATH="core:legacy${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m budgettracker.main "$@"
