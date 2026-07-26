"""Suggest categories for uncategorized transactions via the local LLM.

Runs the sweep in web.app.services.suggest: for every transaction still in
«Otros / sin categoría» (and without a pending suggestion), ask the local
Ollama model to pick one of that user's categories, and store the answer as a
CategorySuggestion the dashboard offers for one-click accept.

Deliberately a cron job, not part of the ingestion webhook: the model runs on
the deploy box's CPU and takes seconds per merchant, and an Ollama outage must
never touch mail processing. If BUDGET_OLLAMA_URL is empty, this is a no-op.

Usage:
    BUDGET_DATABASE_URL=... python scripts/suggest_categories.py [--dry-run]

Cron (every 15 minutes, flock-guarded like the v1 pipeline):
    */15 * * * * cd /srv/app && flock -n /tmp/suggest.lock \
        .venv/bin/python scripts/suggest_categories.py >> suggest.log 2>&1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))  # budgetcore lives in core/

from web.app.db import get_sessionmaker  # noqa: E402
from web.app.services.suggest import sweep  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="call the model but don't persist suggestions")
    args = parser.parse_args()

    with get_sessionmaker()() as session:
        created = sweep(session)
        if args.dry_run:
            session.rollback()
            print(f"[dry-run] would create {created} suggestion(s)")
        else:
            session.commit()
            print(f"created {created} suggestion(s)")


if __name__ == "__main__":
    main()
