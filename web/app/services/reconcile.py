"""Recover received mail that no webhook ever delivered.

Inbound is push-only: Resend POSTs the webhook, and when the endpoint is
unreachable it retries six times over ~22.5 hours and then stops. A box that is
down longer than that loses the mail outright, and the loss is invisible —
"no transactions arrived" looks exactly like "nothing was spent". A 28-hour
outage on 2026-08-06 cost a real card consumption that nothing ever flagged;
this module is the answer to that.

The sweep asks the provider what it received, subtracts what we already hold,
and pushes the difference through the ordinary ingest path. Nothing here
re-implements ingestion: ``handle_inbound`` is idempotent on the provider
message id, and that id *is* the Resend email id, so reconciliation is a set
difference plus the call the webhook would have made. Routing, the bank
registry, dedupe, alerting and retention all behave as they would have live.

Only the Resend path is covered. Mail still forwarded to Postmark is not
reconcilable here — see the module note in ``routers.webhook`` about inbound
being moved one provider at a time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RawEmail
from . import inbound_resend
from .ingest import handle_inbound

log = logging.getLogger("reconcile")

# How long the inbound stream may stay silent before that is itself the alert.
# Comfortably longer than a quiet night, short enough to catch a forward that
# broke yesterday.
STALE_AFTER = timedelta(hours=36)

# `IN (...)` has a host-variable ceiling — SQLite's is the low one — and the id
# list is bounded only by how much mail the provider still keeps.
_ID_CHUNK = 500

# Enough recovered mail named in the alert to recognise it; the rest is a count.
_MAX_LISTED = 10


@dataclass(frozen=True)
class ReconcileResult:
    listed: int
    missing: int
    ingested: int
    failed: int
    recovered: tuple[str, ...]
    newest_received: datetime | None

    @property
    def stale_for(self) -> timedelta | None:
        """How long the inbound stream has been silent, once that is longer than
        STALE_AFTER. ``None`` while mail is flowing normally."""
        if self.newest_received is None:
            return None
        quiet = datetime.now(timezone.utc) - self.newest_received
        return quiet if quiet > STALE_AFTER else None

    @property
    def worth_reporting(self) -> bool:
        """A healthy run stays silent — the owner should only hear from the
        reconciler when it recovered something, could not, or the inbox has
        gone quiet for longer than mail normally pauses."""
        return bool(self.ingested or self.failed or self.stale_for)


def _parse_created(value: object) -> datetime | None:
    """Resend timestamps arrive as `2026-08-06T22:38:40.383Z`."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _label(item: dict) -> str:
    """One recovered email, short enough for a Telegram line."""
    created = str(item.get("created_at") or "")[:16].replace("T", " ")
    sender = str(item.get("from") or "?").strip()
    subject = " ".join(str(item.get("subject") or "").split())[:48]
    return f"{created} · {sender} — {subject}".strip(" —·")


def _known_ids(session: Session, ids: list[str]) -> set[str]:
    """Which of these provider ids are already stored."""
    known: set[str] = set()
    for start in range(0, len(ids), _ID_CHUNK):
        chunk = ids[start:start + _ID_CHUNK]
        known.update(session.scalars(
            select(RawEmail.provider_message_id)
            .where(RawEmail.provider_message_id.in_(chunk))))
    return known


def sweep(session: Session, *, dry_run: bool = False) -> ReconcileResult:
    """Ingest every received email the provider has and we don't.

    Propagates ``ReceivedEmailUnavailable`` if the provider can't be listed at
    all — the caller must not mistake an unreachable API for an empty gap.
    """
    received = inbound_resend.list_received()
    ids = [str(item["id"]) for item in received]
    known = _known_ids(session, ids) if ids else set()

    # Oldest first, so recovered transactions land in the order they really
    # happened and the alerts they fire arrive in that same order.
    missing = [item for item in received if str(item["id"]) not in known]
    missing.reverse()

    newest = max((d for d in (_parse_created(item.get("created_at"))
                              for item in received) if d), default=None)

    ingested = failed = 0
    recovered: list[str] = []

    for item in missing:
        email_id = str(item["id"])
        if dry_run:
            recovered.append(_label(item))
            continue
        try:
            payload = inbound_resend.to_postmark_payload(
                inbound_resend.fetch_email(email_id))
        except inbound_resend.ReceivedEmailUnavailable:
            # Nothing was written, so the id stays missing and the next sweep
            # retries it. One unfetchable email must not strand the others.
            log.exception("could not fetch received email %s", email_id)
            failed += 1
            continue
        try:
            handle_inbound(session, payload)
        except Exception:  # noqa: BLE001 — one bad email, not a dead sweep
            log.exception("reconcile ingest failed for %s", email_id)
            session.rollback()
            failed += 1
            continue
        ingested += 1
        recovered.append(_label(item))

    return ReconcileResult(
        listed=len(received),
        missing=len(missing),
        ingested=ingested,
        failed=failed,
        recovered=tuple(recovered[:_MAX_LISTED]),
        newest_received=newest,
    )
