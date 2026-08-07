"""Re-ingest received mail that no webhook ever delivered.

Runs the sweep in web.app.services.reconcile: list what Resend received,
subtract what we already stored, and push the difference through the ordinary
ingest path. Inbound is push-only, and Resend stops retrying a dead endpoint
after ~22.5 hours — so any outage longer than that silently loses mail, which
reads as "nothing was spent". This is the catch-up that makes it not silent.

Deliberately a cron job rather than something the app does on startup: it makes
outbound calls to the provider and can take a while after a long outage, and a
Resend hiccup must never delay uvicorn coming up.

Usage:
    BUDGET_RESEND_TOKEN=... BUDGET_DATABASE_URL=... \
        python scripts/reconcile_inbound.py [--dry-run]

Cron (at boot once the tunnel is up, then hourly, flock-guarded like the rest):
    @reboot sleep 60 && cd /srv/app && flock -n /tmp/reconcile.lock \
        .venv/bin/python scripts/reconcile_inbound.py >> reconcile.log 2>&1
    17 * * * * cd /srv/app && flock -n /tmp/reconcile.lock \
        .venv/bin/python scripts/reconcile_inbound.py >> reconcile.log 2>&1
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))  # budgetcore lives in core/

from web.app.db import get_sessionmaker  # noqa: E402
from web.app.services import notify, reconcile  # noqa: E402
from web.app.services.inbound_resend import ReceivedEmailUnavailable  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is missing without ingesting it")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_sessionmaker()() as session:
        try:
            result = reconcile.sweep(session, dry_run=args.dry_run)
        except ReceivedEmailUnavailable as exc:
            # Exit non-zero so a run that never reached the provider is
            # distinguishable from one that found nothing missing.
            print(f"[{stamp}] provider unavailable: {exc}", file=sys.stderr)
            return 1

        prefix = "[dry-run] " if args.dry_run else ""
        print(f"[{stamp}] {prefix}listed {result.listed}, "
              f"missing {result.missing}, ingested {result.ingested}, "
              f"failed {result.failed}")
        for item in result.recovered:
            print(f"  {'would ingest' if args.dry_run else 'recovered'}: {item}")
        if stale := result.stale_for:
            print(f"  no inbound mail for {stale.total_seconds() / 3600:.0f}h")

        if args.dry_run:
            session.rollback()
        elif result.worth_reporting:
            stale_hours = (stale.total_seconds() / 3600) if stale else None
            notify.notify_reconcile(session, result.ingested, result.failed,
                                    result.recovered, stale_hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
