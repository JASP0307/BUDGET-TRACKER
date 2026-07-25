"""Inbound-email pipeline: store → route → verify → parse → categorize →
dedupe → persist → notify. Reuses budgetcore for everything domain-shaped.

Every failure mode ends as a `processing_status` on the RawEmail row instead
of an exception escaping the webhook — one bad email must never block others.

**Retention:** the body of an email that parsed successfully is discarded as
soon as its Transaction exists (`_drop_body`). The transaction is the product;
keeping the bank's notification would mean holding the most sensitive data in
the system for no operational reason. The cost is that history can't be
reparsed, which is precisely why bodies are kept for the emails we *failed* to
read — those are the ones a new bank template has to be built from. The
remaining statuses are swept by scripts/purge_raw_emails.py.
"""

from __future__ import annotations

import base64
import email
import logging
import re
from dataclasses import replace
from datetime import date
from email import policy

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from budgetcore.banks import bank_domains
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
# or noise/spoofing. Derived from the bank registry (budgetcore.banks).
KNOWN_BANK_DOMAINS = bank_domains()
GMAIL_CONFIRMATION_SENDER = "forwarding-noreply@google.com"
_TOKEN_RE = re.compile(r"^u_([a-z0-9]{6,32})@", re.IGNORECASE)
_HASH_RE = re.compile(r"^u_([a-z0-9]{6,32})$", re.IGNORECASE)  # Postmark MailboxHash
# The token anywhere it can legitimately appear inside a routing address:
#   u_<token>@in.example.do                     custom inbound domain
#   <pmhash>+u_<token>@inbound.postmarkapp.com  Postmark default inbound
#   user+caf_=<pmhash>+u_<token>=inbound.postmarkapp.com@gmail.com
#                                               Gmail's forwarding Return-Path
# Anchored on a delimiter at both ends so it can't match inside a longer word.
_TOKEN_ANYWHERE_RE = re.compile(r"(?:^|[+=<\s])u_([a-z0-9]{6,32})(?=[@=+>\s]|$)",
                                re.IGNORECASE)
# Headers that name where a message was actually *delivered*. Deliberately a
# short allowlist rather than a scan of every header: routing on header content
# means anyone who learns a token could aim mail at that account, so the fewer
# places we honour it, the better. See the DMARC follow-up in TASKS.md.
_ROUTING_HEADERS = ("X-Forwarded-To", "Delivered-To", "Return-Path",
                    "X-Original-To")
_GMAIL_CODE_RE = re.compile(r"\b(\d{9})\b")  # legacy numeric confirmation code
# Modern Gmail forwarding-confirmation "click to confirm" link (vf- = verify).
_GMAIL_CONFIRM_LINK_RE = re.compile(
    r"https://mail(?:-settings)?\.google\.com/mail/vf-[^\s\"'<>]+")

DEFAULT_USD_DOP = 60.0

# Statuses whose body is dropped the moment processing finishes: we got what we
# needed. Everything else keeps its body for the purge script's timed window —
# `failed`/`unrecognized` because a human has to read them to add bank support,
# `confirmation` because it holds the Gmail link the user still needs.
_DISCARD_BODY_STATUSES = ("processed", "skipped", "backfilled")


def _drop_body(raw: RawEmail) -> None:
    """Discard the archived email body, keeping the row for audit and dedupe."""
    raw.html_body = ""


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
    # Data minimization: drop the body now if nothing further needs it. Runs
    # after _process, which reads the body, and covers the attachment/backfill
    # path too since that sets its status before returning.
    if raw.processing_status in _DISCARD_BODY_STATUSES:
        _drop_body(raw)
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

    # 1b. Backfill: a "Forward as attachment" batch carries the original bank
    # emails as message/rfc822 attachments. Process each; the outer forwarding
    # message (From = the user's own Gmail) is not itself a transaction.
    attachments = _eml_attachments(payload)
    if attachments:
        _process_attachments(session, raw, attachments)
        return

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

    # 3-8. Parse → card → categorize → dedupe → persist → notify.
    status, note = _ingest_transaction(
        session, raw, raw.from_addr, raw.subject, crypto.decrypt(raw.html_body),
        message_id=raw.provider_message_id, notify_user=True)
    raw.processing_status = status
    raw.note = note


def _ingest_transaction(session: Session, raw: RawEmail, from_addr: str,
                        subject: str, html: str, *, message_id: str,
                        notify_user: bool) -> tuple[str, str]:
    """Parse one bank email (already routed to ``raw.user_id``) and persist a
    transaction. Returns ``(status, note)`` for the caller to record — a live
    single message adopts it directly; a backfill batch aggregates.

    Shared by the live webhook path and the "forward as attachment" backfill,
    so both apply the exact same spoofing guard, parser, categorization and
    content dedupe. ``notify_user`` is False for backfill so importing a whole
    month does not fire a Telegram alert per historical charge.
    """
    sender = (from_addr or "").lower()

    # 3. Spoofing guard: only mail originating from a known bank domain.
    if not any(d in sender for d in KNOWN_BANK_DOMAINS):
        return "skipped", f"sender not a known bank: {from_addr}"

    # 4. Parse with budgetcore.
    txn = parse_email(message_id, sender, subject, html,
                      usd_to_dop=current_fx_rate(session))
    if txn is None:
        return "skipped", "bank mail but not a loggable transaction (declined/marketing)"

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
    # new and double-count it. This also collapses a backfilled charge against
    # its live-forwarded twin (and vice-versa), since the key is content-based.
    card_key = f"{card.bank}:{card.last4}"
    key = "|".join(str(p) for p in signature(
        card_key, txn.txn_date, txn.merchant, txn.signed_amount()))
    duplicate = session.scalar(select(Transaction).where(
        Transaction.user_id == raw.user_id, Transaction.dedupe_key == key))
    if duplicate is not None:
        return "skipped", f"duplicate of transaction {duplicate.id}"

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

    # 8. Notify (best effort — a Telegram outage must not fail ingestion).
    if notify_user:
        try:
            spent, budget = month_spend_and_budget(
                session, raw.user_id, category.id if category else None,
                txn.txn_date)
            notify.transaction_alert(session, raw.user_id, txn, spent, budget)
        except Exception:  # noqa: BLE001
            log.exception("notification failed for raw_email %s", raw.id)
    return "processed", ""


def _eml_attachments(payload: dict) -> list[dict]:
    """The forwarded bank emails inside a "forward as attachment" batch: the
    Postmark attachments whose type is message/rfc822 (or name ends .eml)."""
    out = []
    for att in payload.get("Attachments", []) or []:
        if not isinstance(att, dict):
            continue
        ctype = (att.get("ContentType") or "").lower()
        name = (att.get("Name") or "").lower()
        if ctype.startswith("message/rfc822") or name.endswith(".eml"):
            out.append(att)
    return out


def _process_attachments(session: Session, raw: RawEmail,
                         attachments: list[dict]) -> None:
    """Ingest each forwarded bank email in a backfill batch. One archival
    RawEmail (the outer forward) owns all the resulting transactions; a bad
    attachment is counted and skipped, never aborting the rest."""
    processed = skipped = 0
    for i, att in enumerate(attachments):
        try:
            data = base64.b64decode(att.get("Content", "") or "")
            sender, subject, html = _parse_eml(data)
            status, _ = _ingest_transaction(
                session, raw, sender, subject, html,
                message_id=f"{raw.provider_message_id}:{i}", notify_user=False)
        except Exception:  # noqa: BLE001 — one bad attachment must not block the batch
            log.exception("backfill attachment %d failed for raw_email %s", i, raw.id)
            status = "failed"
        processed += status == "processed"
        skipped += status != "processed"
    raw.processing_status = "backfilled"
    raw.note = f"backfilled {processed} transactions ({skipped} skipped)"


def _parse_eml(data: bytes) -> tuple[str, str, str]:
    """Pull (From, Subject, body-HTML) out of a raw .eml. Prefers the text/html
    part, falling back to text/plain; attachments within the .eml are ignored."""
    msg = email.message_from_bytes(data, policy=policy.default)
    html = plain = ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/html" and not html:
            html = part.get_content()
        elif ctype == "text/plain" and not plain:
            plain = part.get_content()
    return msg.get("From", "") or "", msg.get("Subject", "") or "", html or plain


def _route(session: Session, payload: dict):
    """Resolve the inbound address from a payload.

    Sources are tried most-trustworthy first:

    1. ``OriginalRecipient`` — the envelope recipient Postmark actually delivered
       to. The correct answer whenever it is present.
    2. ``MailboxHash`` — Postmark's parse of the ``+`` part.
    3. ``ToFull`` / ``To`` — the header. Only useful when the user mails the
       inbound address directly (manual forward, our own replayed fixtures).
    4. Delivery headers (``_ROUTING_HEADERS``).

    Steps 1 and 4 exist because of a real failure: on **Gmail auto-forwarded**
    bank mail the ``To`` header is the *bank addressing the user*, so the inbound
    address appears nowhere in it, and every such message was filed as
    ``unrecognized``. Gmail records the true destination in ``X-Forwarded-To``
    and in the rewritten ``Return-Path``, which is what step 4 reads.
    """
    tokens: list[str] = []

    def add(value: str | None, *, anywhere: bool = False) -> None:
        value = (value or "").strip()
        if not value:
            return
        if anywhere:
            tokens.extend(m.lower() for m in _TOKEN_ANYWHERE_RE.findall(value))
        else:
            m = _TOKEN_RE.match(value)
            if m:
                tokens.append(m.group(1).lower())

    # 1. Envelope recipient — what the message was really delivered to.
    add(payload.get("OriginalRecipient"), anywhere=True)

    # 2. Postmark default-inbound path: the token is the mailbox hash.
    hashes = [payload.get("MailboxHash", "")]
    hashes += [t.get("MailboxHash", "") for t in payload.get("ToFull", [])
               if isinstance(t, dict)]
    for h in hashes:
        m = _HASH_RE.match((h or "").strip())
        if m:
            tokens.append(m.group(1).lower())

    # 3. Custom-domain path: u_<token>@in.<domain> in the To address.
    candidates = [t.get("Email", "") for t in payload.get("ToFull", [])
                  if isinstance(t, dict)]
    if not candidates and payload.get("To"):
        candidates = [payload["To"]]
    for addr in candidates:
        add(addr)
        add(addr, anywhere=True)  # <pmhash>+u_<token>@… addressed directly

    # 4. Delivery headers, for forwarded mail whose To is the user's own address.
    for header in payload.get("Headers", []) or []:
        if isinstance(header, dict) and header.get("Name") in _ROUTING_HEADERS:
            add(header.get("Value"), anywhere=True)

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
