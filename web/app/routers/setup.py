"""Onboarding: connect the user's card-notification emails.

Three steps, all resumable and driven off live state (polled via /setup/status):
  1. Point Gmail's forwarding at the user's inbound address; Google's
     confirmation link or code is surfaced here the moment it lands.
  2. Import a per-user Gmail filter so only bank mail is forwarded. Gated on
     step 1 — Gmail refuses to import a filter that forwards to an address it
     has not confirmed yet.
  3. A one-time backfill of the current month, since forwarding only catches
     new mail.

Cards are not a step: they are auto-created from the first emails that arrive
and offered for optional labelling afterwards. Categories are seeded at
registration and email is verified before login, so both are already behind the
user by the time they reach here.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from ..templating import templates
from sqlalchemy import func, select

from budgetcore.banks import bank_choices, from_query
from budgetcore.models import Bank

from ..auth.deps import current_user
from ..db import get_sessionmaker
from ..models import Card, InboundAddress, RawEmail, Transaction, User
from ..services import notify
from ..services.inbound import format_inbound_address

router = APIRouter()

# Both derived from the bank registry (budgetcore.banks): the picker of banks a
# user can register, and the Gmail from:(...) value shared by the copyable
# search and the downloadable filter so what the user sees matches the import.
BANKS = bank_choices()
BANK_FROM_QUERY = from_query()
_CODE_RE = re.compile(r"^\d{6,}$")  # legacy numeric confirmation code
_LAST4_RE = re.compile(r"^\d{4}$")


def _backfill_query() -> str:
    """The Gmail search for the one-time backfill (step 3): bank senders, bounded
    to the current month.

    The unbounded ``BANK_FROM_QUERY`` is correct for the *filter*, which has to
    keep forwarding next month, and wrong here. Asking someone to eyeball "this
    month's" mail out of an unbounded result list is how the first real backfill
    imported November 2025: ten transactions that no current-month dashboard ever
    shows, which reads as the product being broken.

    The bound is the last day of the *previous* month, not the 1st, because
    Gmail's ``after:`` is ambiguous about whether it includes the given date. An
    overshoot of one day imports a handful of rows the dashboard won't display;
    an undershoot silently drops the 1st of the month. Overshoot is the safer
    error.
    """
    first = date.today().replace(day=1)
    return f"from:({BANK_FROM_QUERY}) after:{first - timedelta(days=1):%Y/%m/%d}"


def _gmail_filter_xml(forward_to: str) -> str:
    """A Gmail "Export filters" Atom document that forwards only bank mail to the
    user's inbound address, so onboarding is Filters → Import → Create rather
    than hand-building a filter. The forward address must already be a *confirmed*
    forwarding address in the account (step 2) for Gmail to accept the import."""
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<feed xmlns='http://www.w3.org/2005/Atom' "
        "xmlns:apps='http://schemas.google.com/apps/2006'>\n"
        "  <title>Cualto</title>\n"
        "  <entry>\n"
        "    <category term='filter'></category>\n"
        "    <title>Reenviar notificaciones de tarjetas a Cualto</title>\n"
        "    <content></content>\n"
        f"    <apps:property name='from' value={quoteattr(BANK_FROM_QUERY)}/>\n"
        f"    <apps:property name='forwardTo' value={quoteattr(forward_to)}/>\n"
        "  </entry>\n"
        "</feed>\n"
    )


def _inbound_address(session, user_id) -> str | None:
    inbound = session.scalar(select(InboundAddress).where(
        InboundAddress.user_id == user_id, InboundAddress.active.is_(True)))
    if inbound is None:
        return None
    return format_inbound_address(inbound.token)


def _confirmation_source(subject: str) -> str | None:
    """The source Gmail that requested the forward, taken from Google's subject
    ("… Forwarding Confirmation - Receive Mail from <addr>"). Returns the address
    or None. Grabbing the single email in the subject works regardless of the
    subject's language."""
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", subject or "")
    return m.group(0) if m else None


def _mask_email(addr: str) -> str:
    """``mariaperez@gmail.com`` → ``ma***@gmail.com``. Enough for someone to
    recognize their own other mailbox, not enough to disclose a stranger's."""
    local, _, domain = (addr or "").partition("@")
    if not domain:
        return "***"
    # Short local parts would otherwise be revealed whole by a 2-char prefix.
    head = local[:2] if len(local) > 3 else local[:1]
    return f"{head}***@{domain}"


def _confirmation_state(session, user_id, own_email: str) -> tuple[list[dict], str | None]:
    """What /setup should say about forwarding confirmations, as
    ``(confirmations, mismatch_source)``.

    ``confirmations`` holds the one whose source Gmail matches this account's
    own registered email, as ``{"kind": "link"|"code", "value", "source"}``.
    Anyone who knows a user's inbound address could set up forwarding to it, so
    the confirmations that land there aren't all the user's own. Restricting to
    the registered email means a user only ever sees (and is asked to confirm)
    the forward from their own Gmail — no one else's address leaks onto their
    setup page, and the list stays a single expected entry in the multi-user
    case. Returned as a list so the template/poller stay uniform; it holds 0 or
    1 items.

    ``mismatch_source`` is the *masked* source of the newest confirmation that
    was filtered out, and only when nothing matched. Dropping those silently is
    what made the common "signed up with one address, forwarding from another"
    mistake unfixable: the page sat on "waiting for Google's confirmation"
    forever with nothing to act on. Naming it — masked, and without ever
    offering its link — keeps the disclosure property above while telling the
    user what actually went wrong.
    """
    own = (own_email or "").strip().lower()
    if not own:
        return [], None
    rows = session.scalars(
        select(RawEmail).where(RawEmail.user_id == user_id,
                               RawEmail.processing_status == "confirmation")
        .order_by(RawEmail.received_at.desc())).all()
    mismatch = None
    for raw in rows:
        source = _confirmation_source(raw.subject)
        if not source:
            continue
        if source.lower() != own:
            # A forward set up from some other Gmail — usually the user's own
            # second account, occasionally someone else's.
            mismatch = mismatch or source
            continue
        note = (raw.note or "")
        if note.startswith("http"):
            return [{"kind": "link", "value": note, "source": source}], None
        if _CODE_RE.match(note):
            return [{"kind": "code", "value": note, "source": source}], None
    return [], _mask_email(mismatch) if mismatch else None


def _rejected_forwards(session, user_id) -> list[str]:
    """Mailboxes whose forwards we refused for this account, newest first.

    A rejected forward is otherwise invisible: it produces no transaction and no
    error anyone sees. Surfacing the sending address turns the common case — the
    user forwarded from a second mailbox they never registered — into something
    they can fix, instead of mail that silently disappears.
    """
    rows = session.scalars(
        select(RawEmail).where(RawEmail.user_id == user_id,
                               RawEmail.processing_status == "rejected")
        .order_by(RawEmail.received_at.desc()).limit(20)).all()
    seen: list[str] = []
    for raw in rows:
        m = re.search(r"forwarded by ([^,]+),", raw.note or "")
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


@router.get("/setup")
def setup_page(request: Request, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        # The manual "Efectivo / otro" bucket is not a detected card: there is
        # nothing to review or relabel about it, and it has no bank behind it.
        cards = session.scalars(select(Card)
                                .where(Card.user_id == user.id,
                                       Card.bank != Bank.MANUAL.value)
                                .order_by(Card.needs_review.desc(),
                                          Card.bank, Card.last4)).all()
        tx_count = session.scalar(select(func.count()).select_from(Transaction)
                                  .where(Transaction.user_id == user.id)) or 0
        confirmations, mismatch = _confirmation_state(session, user.id, user.email)
        return templates.TemplateResponse(request, "setup.html", {
            "banks": BANKS,
            "cards": cards,
            "inbound_addr": _inbound_address(session, user.id),
            "bank_from_query": BANK_FROM_QUERY,
            "backfill_query": _backfill_query(),
            "confirmations": confirmations,
            "mismatch_source": mismatch,
            "own_email": user.email,
            "rejected_forwards": _rejected_forwards(session, user.id),
            "tx_count": tx_count,
            "telegram_visible": notify.telegram_visible(session, user),
        })


@router.get("/setup/gmail-filter.xml")
def gmail_filter(user: User = Depends(current_user)):
    """Per-user Gmail filter the onboarding page offers for one-click import."""
    with get_sessionmaker()() as session:
        addr = _inbound_address(session, user.id)
    if addr is None:
        return Response(status_code=404)
    return Response(
        _gmail_filter_xml(addr), media_type="application/xml",
        headers={"Content-Disposition":
                 'attachment; filename="budget-tracker-gmail-filter.xml"'})


@router.get("/setup/status")
def setup_status(user: User = Depends(current_user)) -> dict:
    """Polled by the setup page to surface the confirmation (code or link), a
    confirmation that arrived from the wrong Gmail, and the first transaction,
    without a full reload."""
    with get_sessionmaker()() as session:
        tx_count = session.scalar(select(func.count()).select_from(Transaction)
                                  .where(Transaction.user_id == user.id)) or 0
        confirmations, mismatch = _confirmation_state(session, user.id, user.email)
        return {
            "confirmations": confirmations,
            "mismatch_source": mismatch,
            "tx_count": int(tx_count),
        }


@router.post("/cards")
def add_card(bank: str = Form(...), last4: str = Form(...),
             label: str = Form(""), user: User = Depends(current_user)):
    bank = bank.strip().lower()
    last4 = last4.strip()
    label = label.strip()[:80] or None
    if bank not in dict(BANKS) or not _LAST4_RE.match(last4):
        return RedirectResponse("/setup", status_code=303)
    with get_sessionmaker()() as session:
        # A card may already exist (auto-created on ingest, needs_review):
        # confirm and label it rather than erroring on the unique constraint.
        card = session.scalar(select(Card).where(
            Card.user_id == user.id, Card.bank == bank, Card.last4 == last4))
        if card is None:
            session.add(Card(user_id=user.id, bank=bank, last4=last4,
                             label=label, needs_review=False))
        else:
            card.label = label or card.label
            card.needs_review = False
        session.commit()
    return RedirectResponse("/setup", status_code=303)


@router.post("/cards/{card_id}/label")
def label_card(card_id: uuid.UUID, label: str = Form(""),
               user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        card = session.get(Card, card_id)
        if card is not None and card.user_id == user.id:
            card.label = label.strip()[:80] or None
            card.needs_review = False
            session.commit()
    return RedirectResponse("/setup", status_code=303)


@router.post("/cards/{card_id}/delete")
def delete_card(card_id: uuid.UUID, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        card = session.get(Card, card_id)
        if card is not None and card.user_id == user.id:
            has_txns = session.scalar(
                select(func.count()).select_from(Transaction)
                .where(Transaction.card_id == card.id))
            # Keep cards that own transactions (FK + history); just deactivate.
            if has_txns:
                card.active = False
            else:
                session.delete(card)
            session.commit()
    return RedirectResponse("/setup", status_code=303)
