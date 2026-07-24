"""Inbound-email pipeline: store → route → verify → parse → categorize →
dedupe → persist → notify. Reuses budgetcore for everything domain-shaped.

Every failure mode ends as a `processing_status` on the RawEmail row instead
of an exception escaping the webhook — one bad email must never block others.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from budgetcore.categorize import UNCATEGORIZED, categorize
from budgetcore.dedupe import signature
from budgetcore.parsers import parse_email

from .. import crypto
from ..models import (Budget, Card, Category, FxRate, InboundAddress,
                      RawEmail, Rule, Transaction)
from . import notify

log = logging.getLogger("ingest")

# Auto-forwarded Gmail keeps the original bank as the From header; anything
# else mailed to an inbound address is either Gmail's forwarding-confirmation
# or noise/spoofing.
KNOWN_BANK_DOMAINS = ("popularenlinea.com", "qik.do")
GMAIL_CONFIRMATION_SENDER = "forwarding-noreply@google.com"
_TOKEN_RE = re.compile(r"^u_([a-z0-9]{6,32})@", re.IGNORECASE)
_HASH_RE = re.compile(r"^u_([a-z0-9]{6,32})$", re.IGNORECASE)  # Postmark MailboxHash
_GMAIL_CODE_RE = re.compile(r"\b(\d{9})\b")  # legacy numeric confirmation code
# Modern Gmail forwarding-confirmation "click to confirm" link (vf- = verify).
_GMAIL_CONFIRM_LINK_RE = re.compile(
    r"https://mail(?:-settings)?\.google\.com/mail/vf-[^\s\"'<>]+")

DEFAULT_USD_DOP = 60.0


def handle_inbound(session: Session, payload: dict) -> RawEmail:
    """Entry point for the Postmark webhook. Idempotent on MessageID."""
    provider_id = payload.get("MessageID") or ""
    existing = session.scalar(
        select(RawEmail).where(RawEmail.provider_message_id == provider_id))
    if existing is not None:
        return existing  # webhook retry

    raw = RawEmail(
        provider_message_id=provider_id,
        from_addr=(payload.get("From") or "")[:320],
        subject=(payload.get("Subject") or "")[:500],
        html_body=crypto.encrypt(payload.get("HtmlBody") or payload.get("TextBody") or ""),
        headers={h["Name"]: h["Value"] for h in payload.get("Headers", [])
                 if isinstance(h, dict) and "Name" in h},
    )
    session.add(raw)
    session.flush()

    try:
        _process(session, raw, payload)
    except Exception:  # noqa: BLE001 — archive the failure, keep serving
        log.exception("ingest failed for raw_email %s", raw.id)
        raw.processing_status = "failed"
        raw.note = "unexpected error — see logs"
    session.commit()
    return raw


def _process(session: Session, raw: RawEmail, payload: dict) -> None:
    # 1. Route by the u_<token> local part of the To address.
    address = _route(session, payload)
    if address is None:
        raw.processing_status = "unrecognized"
        raw.note = "no inbound address matched To:"
        return
    raw.inbound_address_id = address.id
    raw.user_id = address.user_id

    sender = raw.from_addr.lower()

    # 2. Gmail's forwarding-confirmation — surface it for onboarding. Modern
    # Gmail sends a "click to confirm" link (vf-) and no numeric code, so prefer
    # the link; fall back to the legacy 9-digit code if that's all there is.
    if GMAIL_CONFIRMATION_SENDER in sender:
        raw.processing_status = "confirmation"
        body = crypto.decrypt(raw.html_body)
        link = _GMAIL_CONFIRM_LINK_RE.search(body)
        code = _GMAIL_CODE_RE.search(body) or _GMAIL_CODE_RE.search(raw.subject)
        if link:
            raw.note = link.group(0)
        elif code:
            raw.note = code.group(1)
        else:
            raw.note = "confirmation email (no code or link found)"
        return

    # 3. Spoofing guard: only mail originating from a known bank domain.
    if not any(d in sender for d in KNOWN_BANK_DOMAINS):
        raw.processing_status = "skipped"
        raw.note = f"sender not a known bank: {raw.from_addr}"
        return

    # 4. Parse with budgetcore.
    txn = parse_email(raw.provider_message_id, sender, raw.subject,
                      crypto.decrypt(raw.html_body),
                      usd_to_dop=current_fx_rate(session))
    if txn is None:
        raw.processing_status = "skipped"
        raw.note = "bank mail but not a loggable transaction (declined/marketing)"
        return

    # 5. Card registry: never drop a transaction over an unregistered card.
    card = _get_or_create_card(session, raw.user_id, txn.bank.value, txn.last4)
    txn = replace(txn, card=card.display)

    # 6. Categorize with the user's rules (priority order, first match wins).
    rules = _rules_dict(session, raw.user_id)
    txn = categorize(txn, rules)
    category = session.scalar(select(Category).where(
        Category.user_id == raw.user_id, Category.name == txn.category))

    # 7. Content dedupe. Key on the card's STABLE identity (bank+last4), not its
    # display label — a user relabeling a card must not make the same charge look
    # new and double-count it.
    card_key = f"{card.bank}:{card.last4}"
    key = "|".join(str(p) for p in signature(
        card_key, txn.txn_date, txn.merchant, txn.signed_amount()))
    duplicate = session.scalar(select(Transaction).where(
        Transaction.user_id == raw.user_id, Transaction.dedupe_key == key))
    if duplicate is not None:
        raw.processing_status = "skipped"
        raw.note = f"duplicate of transaction {duplicate.id}"
        return

    row = Transaction(
        user_id=raw.user_id,
        raw_email_id=raw.id,
        card_id=card.id,
        tx_type=txn.tx_type.value,
        merchant=txn.merchant,
        txn_date=txn.txn_date,
        original_amount=txn.original_amount,
        currency=txn.currency,
        fx_rate_used=(current_fx_rate(session) if txn.currency == "US$" else 1.0),
        amount_dop=txn.signed_amount(),
        category_id=category.id if category else None,
        dedupe_key=key,
    )
    session.add(row)
    raw.processing_status = "processed"

    # 8. Notify (best effort — a Telegram outage must not fail ingestion).
    try:
        spent, budget = month_spend_and_budget(
            session, raw.user_id, category.id if category else None, txn.txn_date)
        notify.transaction_alert(session, raw.user_id, txn, spent, budget)
    except Exception:  # noqa: BLE001
        log.exception("notification failed for raw_email %s", raw.id)


def _route(session: Session, payload: dict):
    """Resolve the inbound address from a payload, supporting both shapes:
    a custom-domain To (`u_<token>@in.<domain>`) and Postmark's default inbound
    where the token rides in the mailbox hash (`<pmhash>+u_<token>@...`)."""
    tokens: list[str] = []

    # Custom-domain path: u_<token>@in.<domain> in the To address.
    candidates = [t.get("Email", "") for t in payload.get("ToFull", [])
                  if isinstance(t, dict)]
    if not candidates and payload.get("To"):
        candidates = [payload["To"]]
    for addr in candidates:
        m = _TOKEN_RE.match(addr.strip())
        if m:
            tokens.append(m.group(1).lower())

    # Postmark default-inbound path: the token is the mailbox hash.
    hashes = [payload.get("MailboxHash", "")]
    hashes += [t.get("MailboxHash", "") for t in payload.get("ToFull", [])
               if isinstance(t, dict)]
    for h in hashes:
        m = _HASH_RE.match((h or "").strip())
        if m:
            tokens.append(m.group(1).lower())

    for token in tokens:
        found = session.scalar(select(InboundAddress).where(
            InboundAddress.token == token, InboundAddress.active.is_(True)))
        if found:
            return found
    return None


def _get_or_create_card(session: Session, user_id, bank: str, last4: str) -> Card:
    card = session.scalar(select(Card).where(
        Card.user_id == user_id, Card.bank == bank, Card.last4 == last4))
    if card is None:
        card = Card(user_id=user_id, bank=bank, last4=last4, needs_review=True)
        session.add(card)
        session.flush()
    return card


def _rules_dict(session: Session, user_id) -> dict[str, str]:
    rows = session.scalars(select(Rule).where(Rule.user_id == user_id)
                           .order_by(Rule.priority)).all()
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r.substring, r.category.name)
    return out


def recategorize_uncategorized(session: Session, user_id) -> int:
    """Apply the user's rules to their already-stored, still-uncategorized
    transactions. Ingestion categorizes on arrival; this backfills the existing
    dashboard when a rule is added (or edited) later.

    Only transactions currently in UNCATEGORIZED (or with no category) are
    touched, so manual recategorizations and withdrawals are left alone. The
    first rule to match by priority wins — the same precedence ingestion uses.
    Returns the number of transactions moved. Caller commits.
    """
    rules = session.scalars(select(Rule).where(Rule.user_id == user_id)
                            .order_by(Rule.priority)).all()
    if not rules:
        return 0

    uncat = session.scalar(select(Category).where(
        Category.user_id == user_id, Category.name == UNCATEGORIZED))
    txns = session.scalars(select(Transaction).where(
        Transaction.user_id == user_id,
        or_(Transaction.category_id.is_(None),
            Transaction.category_id == (uncat.id if uncat else None)))).all()

    moved = 0
    for txn in txns:
        merchant = (txn.merchant or "").upper()
        for rule in rules:
            if rule.substring.upper() in merchant:
                if txn.category_id != rule.category_id:
                    txn.category_id = rule.category_id
                    moved += 1
                break
    return moved


def current_fx_rate(session: Session) -> float:
    rate = session.scalar(select(FxRate).where(FxRate.pair == "USD/DOP")
                          .order_by(FxRate.effective_date.desc()).limit(1))
    return rate.rate if rate else DEFAULT_USD_DOP


def month_spend_and_budget(session: Session, user_id, category_id,
                           when: date) -> tuple[float, float]:
    month_start = when.replace(day=1)
    spent = session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_dop), 0.0)).where(
            Transaction.user_id == user_id,
            Transaction.category_id == category_id,
            Transaction.txn_date >= month_start,
            Transaction.txn_date < _next_month(month_start))) or 0.0
    budget_row = session.scalar(select(Budget).where(
        Budget.user_id == user_id, Budget.category_id == category_id,
        Budget.month == month_start))
    return float(spent), float(budget_row.amount_dop if budget_row else 0.0)


def _next_month(month_start: date) -> date:
    return (month_start.replace(year=month_start.year + 1, month=1)
            if month_start.month == 12
            else month_start.replace(month=month_start.month + 1))
