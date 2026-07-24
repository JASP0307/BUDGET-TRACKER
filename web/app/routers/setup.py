"""Onboarding: connect the user's card-notification emails.

Three steps, all resumable and driven off live state:
  1. Register cards (bank + last4 + label).
  2. Forward bank mail to the user's inbound address; Google's confirmation
     code is surfaced here the moment it lands (polled via /setup/status).
  3. Confirm the round-trip once the first real transaction arrives.

Categories are seeded at registration, and email is verified before login,
so those plan steps are already behind the user by the time they reach here.
"""

from __future__ import annotations

import re
import uuid
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from ..templating import templates
from sqlalchemy import func, select

from budgetcore.banks import bank_choices, from_query

from ..auth.deps import current_user
from ..db import get_sessionmaker
from ..models import Card, InboundAddress, RawEmail, Transaction, User
from ..services.inbound import format_inbound_address

router = APIRouter()

# Both derived from the bank registry (budgetcore.banks): the picker of banks a
# user can register, and the Gmail from:(...) value shared by the copyable
# search and the downloadable filter so what the user sees matches the import.
BANKS = bank_choices()
BANK_FROM_QUERY = from_query()
_CODE_RE = re.compile(r"^\d{6,}$")  # legacy numeric confirmation code
_LAST4_RE = re.compile(r"^\d{4}$")


def _gmail_filter_xml(forward_to: str) -> str:
    """A Gmail "Export filters" Atom document that forwards only bank mail to the
    user's inbound address, so onboarding is Filters → Import → Create rather
    than hand-building a filter. The forward address must already be a *confirmed*
    forwarding address in the account (step 2) for Gmail to accept the import."""
    return (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<feed xmlns='http://www.w3.org/2005/Atom' "
        "xmlns:apps='http://schemas.google.com/apps/2006'>\n"
        "  <title>Budget Tracker</title>\n"
        "  <entry>\n"
        "    <category term='filter'></category>\n"
        "    <title>Reenviar notificaciones de tarjetas a Budget Tracker</title>\n"
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


def _latest_confirmation(session, user_id) -> dict | None:
    """The most recent Gmail forwarding-confirmation to surface on /setup, as
    ``{"kind": "link"|"code", "value": ...}``. Modern Gmail sends a click-to-
    confirm link; older messages carried a numeric code."""
    raw = session.scalar(
        select(RawEmail).where(RawEmail.user_id == user_id,
                               RawEmail.processing_status == "confirmation")
        .order_by(RawEmail.received_at.desc()).limit(1))
    note = (raw.note if raw else "") or ""
    if note.startswith("http"):
        return {"kind": "link", "value": note}
    if _CODE_RE.match(note):
        return {"kind": "code", "value": note}
    return None


@router.get("/setup")
def setup_page(request: Request, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        cards = session.scalars(select(Card).where(Card.user_id == user.id)
                                .order_by(Card.needs_review.desc(),
                                          Card.bank, Card.last4)).all()
        tx_count = session.scalar(select(func.count()).select_from(Transaction)
                                  .where(Transaction.user_id == user.id)) or 0
        return templates.TemplateResponse(request, "setup.html", {
            "banks": BANKS,
            "cards": cards,
            "inbound_addr": _inbound_address(session, user.id),
            "bank_from_query": BANK_FROM_QUERY,
            "confirmation": _latest_confirmation(session, user.id),
            "tx_count": tx_count,
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
    """Polled by the setup page to surface the confirmation (code or link) and
    the first transaction without a full reload."""
    with get_sessionmaker()() as session:
        tx_count = session.scalar(select(func.count()).select_from(Transaction)
                                  .where(Transaction.user_id == user.id)) or 0
        return {
            "confirmation": _latest_confirmation(session, user.id),
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
