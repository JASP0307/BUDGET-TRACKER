"""Enforce the raw-email retention policy. Run daily from cron.

Successfully parsed emails already lose their body at ingestion time (see
services.ingest._drop_body). This sweep handles the rest, so nothing sensitive
accumulates just because a parser failed or a user never finished onboarding:

- `confirmation` bodies (Gmail's forwarding link) are blanked after 7 days —
  the link is short-lived anyway, and onboarding is long over by then.
- `failed` / `unrecognized` bodies are blanked after 30 days. That is the window
  to add support for a new bank format; past it, the sample isn't worth keeping.
- Any RawEmail row older than 30 days is deleted outright, unless a Transaction
  still points at it — the transaction is the user's data and must not lose its
  provenance link. Those rows keep their (already empty) body.

Usage:
    BUDGET_DATABASE_URL=... python scripts/purge_raw_emails.py [--dry-run]

Cron (daily at 3:30 AM):
    30 3 * * * cd /srv/app && .venv/bin/python scripts/purge_raw_emails.py >> purge.log 2>&1
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from web.app.db import get_sessionmaker  # noqa: E402
from web.app.models import RawEmail, Transaction  # noqa: E402

CONFIRMATION_BODY_DAYS = 7
PROBLEM_BODY_DAYS = 30
ROW_DAYS = 30


def purge(session: Session, *, now: datetime | None = None,
          dry_run: bool = False) -> dict[str, int]:
    """Apply the retention policy. Returns counts for logging. Caller commits
    unless dry_run."""
    now = now or datetime.now(timezone.utc)
    counts = {"confirmation_bodies": 0, "problem_bodies": 0, "rows_deleted": 0}

    def _blank_bodies(statuses: tuple[str, ...], older_than_days: int) -> int:
        cutoff = now - timedelta(days=older_than_days)
        rows = session.scalars(select(RawEmail).where(
            RawEmail.processing_status.in_(statuses),
            RawEmail.received_at < cutoff,
            RawEmail.html_body != "")).all()
        for row in rows:
            row.html_body = ""
        return len(rows)

    counts["confirmation_bodies"] = _blank_bodies(("confirmation",),
                                                 CONFIRMATION_BODY_DAYS)
    counts["problem_bodies"] = _blank_bodies(("failed", "unrecognized"),
                                             PROBLEM_BODY_DAYS)

    # Row deletion, skipping anything a Transaction still references.
    cutoff = now - timedelta(days=ROW_DAYS)
    referenced = set(session.scalars(
        select(Transaction.raw_email_id).where(
            Transaction.raw_email_id.is_not(None))).all())
    stale = session.scalars(select(RawEmail).where(
        RawEmail.received_at < cutoff)).all()
    for row in stale:
        if row.id in referenced:
            continue
        session.delete(row)
        counts["rows_deleted"] += 1

    if dry_run:
        session.rollback()
    else:
        session.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()

    with get_sessionmaker()() as session:
        counts = purge(session, dry_run=args.dry_run)

    prefix = "would purge" if args.dry_run else "purged"
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {prefix}: "
          f"{counts['confirmation_bodies']} confirmation bodies, "
          f"{counts['problem_bodies']} problem bodies, "
          f"{counts['rows_deleted']} rows deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
