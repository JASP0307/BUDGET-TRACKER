"""Undo one email's ingestion: delete the transactions it produced.

For mail that landed on the wrong account. Routing resolves an account from the
`u_<token>` address alone, so forwarding a batch to the wrong inbound address
files somebody else's transactions onto your dashboard — which is exactly what
happened on 2026-07-26, when a month-backfill forwarded from a second Gmail put
ten November charges on another account.

Ingest now refuses forwards from unverified mailboxes (services.ingest, the
trust gate), so this is the repair tool for anything that got through before —
and for the next time a rule change makes some already-ingested batch wrong.

Cards are reported, never deleted. `_get_or_create_card` may have created a card
for this batch, but the same card can equally be one the user legitimately owns
and has other transactions on; deleting it would take their labels with it.

Usage:
    BUDGET_DATABASE_URL=... python scripts/unroute_raw_email.py <raw_email_id> [--yes]

`raw_email_id` may be an id prefix, so the short form printed by most queries
works. Dry-run unless --yes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from web.app.db import get_sessionmaker  # noqa: E402
from web.app.models import Card, RawEmail, Transaction, User  # noqa: E402

REJECTED_NOTE = "unrouted by hand: forwarded to the wrong account"


def _find(session: Session, wanted: str) -> RawEmail:
    """The one RawEmail whose id starts with `wanted`. Refuses to guess."""
    key = wanted.replace("-", "").lower()
    rows = [r for r in session.scalars(select(RawEmail)).all()
            if str(r.id).replace("-", "").lower().startswith(key)]
    if not rows:
        raise SystemExit(f"no raw_email matches {wanted!r}")
    if len(rows) > 1:
        raise SystemExit(
            f"{wanted!r} matches {len(rows)} rows; use a longer prefix")
    return rows[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw_email_id", help="full id or a unique prefix")
    ap.add_argument("--yes", action="store_true",
                    help="actually delete (default is a dry run)")
    args = ap.parse_args()

    with get_sessionmaker()() as session:
        raw = _find(session, args.raw_email_id)
        owner = session.get(User, raw.user_id) if raw.user_id else None
        txns = session.scalars(select(Transaction).where(
            Transaction.raw_email_id == raw.id)
            .order_by(Transaction.txn_date)).all()

        print(f"raw_email  {raw.id}")
        print(f"  from     {raw.from_addr}")
        print(f"  subject  {raw.subject}")
        print(f"  status   {raw.processing_status} — {raw.note}")
        print(f"  account  {owner.email if owner else '(unrouted)'}")
        print(f"  received {raw.received_at}")
        print(f"\n{len(txns)} transaction(s) would be deleted:")
        for t in txns:
            card = session.get(Card, t.card_id)
            label = f"{card.bank}:{card.last4}" if card else "?"
            print(f"  {t.txn_date}  {t.merchant:<32} {t.amount_dop:>10.2f}  {label}")

        # Cards that this batch is the only evidence for. Reported so the user
        # can decide; auto-deleting would risk taking a real card's label away.
        card_ids = {t.card_id for t in txns if t.card_id}
        orphaned = []
        for card_id in card_ids:
            others = session.scalar(
                select(func.count()).select_from(Transaction).where(
                    Transaction.card_id == card_id,
                    Transaction.raw_email_id != raw.id)) or 0
            if others == 0:
                card = session.get(Card, card_id)
                if card is not None:
                    orphaned.append(card)
        if orphaned:
            print("\nCards left with no transactions (review by hand, not deleted):")
            for card in orphaned:
                print(f"  {card.bank}:{card.last4}  label={card.label!r}")

        if not args.yes:
            print("\nDry run. Re-run with --yes to apply.")
            return 0

        for t in txns:
            session.delete(t)
        raw.processing_status = "rejected"
        raw.note = REJECTED_NOTE
        session.commit()
        print(f"\nDeleted {len(txns)} transaction(s); "
              f"raw_email {raw.id} marked rejected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
