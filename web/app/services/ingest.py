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
from budgetcore.models import Bank
from budgetcore.parsers import parse_email

from .. import crypto
from ..models import (Budget, Card, Category, FxRate, InboundAddress,
                      RawEmail, Rule, Transaction, User, VerifiedForwarder)
from . import notify

log = logging.getLogger("ingest")

# Auto-forwarded mail keeps the original bank as the From header, whichever
# provider does the forwarding; anything else mailed to an inbound address is
# either Gmail's forwarding-confirmation or noise/spoofing. Derived from the
# bank registry (budgetcore.banks).
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
# Lower-cased, and compared lower-cased: header names are case-insensitive on
# the wire and providers disagree in practice — Postmark sends canonical case,
# Resend sends all-lowercase. Matching literally meant forwarded mail arriving
# through Resend read none of these and would have routed nowhere.
_ROUTING_HEADERS = frozenset({"x-forwarded-to", "delivered-to", "return-path",
                              "x-original-to"})
_GMAIL_CODE_RE = re.compile(r"\b(\d{9})\b")  # legacy numeric confirmation code
# Gmail's SRS rewrite of Return-Path on auto-forwarded mail:
#   <jabner0703+caf_=<pmhash>+u_<token>=inbound.postmarkapp.com@gmail.com>
# Everything before "+caf_=" plus the domain is the *forwarding* account, which
# is the only place auto-forwarded mail names it — From is the bank.
_SRS_RETURN_PATH_RE = re.compile(
    r"<?([\w.\-]+)\+caf_=[^@\s>]*@([\w.\-]+\.\w+)>?", re.IGNORECASE)
# Standard SRS — what Microsoft, Postfix and cPanel emit, i.e. every forwarder
# that isn't Gmail: SRS0=<hash>=<tt>=<origdomain>=<origlocal>@<forwarder>.
# Unlike Gmail's +caf_= rewrite this encodes the *original sender* re-hosted at
# the forwarder's domain; the forwarding mailbox is not in there at all, so
# there is nothing to attribute. Matched purely so `_ANY_ADDR_RE` can't mistake
# the token's tail (`=<origlocal>@<forwarder>`) for a forwarding address — on a
# Hotmail user's bank mail that reads as `notificaciones@outlook.com`, which is
# in nobody's trusted set, and every real transaction was rejected.
_SRS_TOKEN_RE = re.compile(
    r"(?:^|[<\s])SRS[01][=+-][^@\s>]*@[\w.\-]+\.\w+", re.IGNORECASE)
_ANY_ADDR_RE = re.compile(r"[\w.+\-]+@[\w.\-]+\.\w+")
# Headers naming the mailbox that performed the forward, most explicit first.
_FORWARDER_HEADERS = ("return-path", "x-forwarded-for")
# Modern Gmail forwarding-confirmation "click to confirm" link (vf- = verify).
_GMAIL_CONFIRM_LINK_RE = re.compile(
    r"https://mail(?:-settings)?\.google\.com/mail/vf-[^\s\"'<>]+")

DEFAULT_USD_DOP = 60.0

# The single pseudo-card every user's hand-entered spend hangs off of. The last4
# is a placeholder — Bank.MANUAL is what identifies it — and both values feed the
# card's dedupe identity, so neither may change once rows exist.
MANUAL_LAST4 = "0000"
MANUAL_CARD_LABEL = "Efectivo / otro"

# Statuses whose body is dropped the moment processing finishes: we got what we
# needed. Everything else keeps its body for the purge script's timed window —
# `failed`/`unrecognized` because a human has to read them to add bank support,
# `confirmation` because it holds the Gmail link the user still needs.
_DISCARD_BODY_STATUSES = ("processed", "skipped", "backfilled")

# The one `skipped` reason that is a *judgement call by our parser* rather than a
# fact about the sender: the mail came from a real bank and we decided it wasn't
# a transaction. If that call is ever wrong, this body is the only evidence — so
# it is kept for the purge window like `unrecognized`, instead of being dropped
# with the spam and the duplicates. Learned the hard way: a real Qik purchase
# notification was skipped this way and its body was gone before anyone could
# check whether the classification was right (2026-07-25).
NOT_LOGGABLE_NOTE = "bank mail but not a loggable transaction (declined/marketing)"


def _keeps_body(status: str, note: str) -> bool:
    """True when the archived body is still worth holding for debugging."""
    if status not in _DISCARD_BODY_STATUSES:
        return True
    return status == "skipped" and note == NOT_LOGGABLE_NOTE


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
    if not _keeps_body(raw.processing_status, raw.note or ""):
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

    sender = raw.from_addr.lower()

    # 2. Gmail's forwarding-confirmation — surface it for onboarding. Modern
    # Gmail sends a "click to confirm" link (vf-) and no numeric code, so prefer
    # the link; fall back to the legacy 9-digit code if that's all there is.
    # Checked before the trust gate below: this one legitimately arrives from
    # Google rather than the user's mailbox, and rejecting it breaks onboarding.
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

    # 2b. Trust gate: routing put this message on an account purely on a token
    # anyone could learn, so require the forwarding mailbox to belong to that
    # account. Placed before the attachment branch so a misdirected backfill is
    # rejected whole rather than attachment by attachment.
    forwarder = _forwarding_account(payload, raw.from_addr)
    if forwarder is not None:
        trusted = _trusted_forwarders(session, raw.user_id)
        if forwarder not in trusted:
            log.warning("rejected raw_email %s: forwarded by %s, not verified "
                        "for user %s", raw.id, forwarder, raw.user_id)
            raw.processing_status = "rejected"
            raw.note = (f"forwarded by {forwarder}, not a verified sender for "
                        "this account")
            return
    else:
        # Deliberately lenient. Over-strict delivery-header logic silently broke
        # live ingestion for days (see TASKS.md); an unidentifiable forwarder
        # must not repeat that. The spoofed-From vector this leaves open is the
        # DMARC/SPF follow-up, not something this gate claims to close.
        log.warning("raw_email %s: could not identify a forwarding mailbox; "
                    "ingesting anyway", raw.id)

    # 2c. Backfill: a "Forward as attachment" batch carries the original bank
    # emails as message/rfc822 attachments. Process each; the outer forwarding
    # message (From = the user's own Gmail) is not itself a transaction.
    attachments = _eml_attachments(payload)
    if attachments:
        _process_attachments(session, raw, attachments)
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
        return "skipped", NOT_LOGGABLE_NOTE

    # 5. Card registry: never drop a transaction over an unregistered card.
    card = _get_or_create_card(session, raw.user_id, txn.bank.value, txn.last4)
    txn = replace(txn, card=card.display)

    # 6. Categorize with the user's rules (priority order, first match wins).
    rules = _rules_dict(session, raw.user_id)
    txn = categorize(txn, rules)
    category = session.scalar(select(Category).where(
        Category.user_id == raw.user_id, Category.name == txn.category))

    # 7. Content dedupe — collapses a backfilled charge against its
    # live-forwarded twin (and vice-versa), since the key is content-based.
    key = dedupe_key_for(card, txn.txn_date, txn.merchant, txn.signed_amount())
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
            notify.transaction_alert(
                session, raw.user_id, txn, spent, budget,
                category_id=category.id if category else None,
                category_name=category.name if category else None)
        except Exception:  # noqa: BLE001
            log.exception("notification failed for raw_email %s", raw.id)
    return "processed", ""


def _header_values(payload: dict, name: str) -> list[str]:
    """Every value of a header, matched case-insensitively and list-tolerantly.

    Same two reasons as ``_ROUTING_HEADERS``: providers disagree on header case
    (Postmark canonical, Resend lower), and a re-forwarded message repeats a
    header, which some providers hand over as a list.
    """
    out: list[str] = []
    for header in payload.get("Headers", []) or []:
        if not isinstance(header, dict):
            continue
        if str(header.get("Name") or "").lower() != name:
            continue
        value = header.get("Value")
        values = value if isinstance(value, (list, tuple)) else [value]
        out.extend(str(v).strip() for v in values if str(v or "").strip())
    return out


def _forwarding_account(payload: dict, from_addr: str) -> str | None:
    """The mailbox that forwarded this message to us, lowercased, or None.

    Two shapes, because the two ways a user can feed us mail put the forwarder
    in different places:

    - **Gmail auto-forward** — ``From`` is the bank addressing the user, so the
      forwarder appears only in Gmail's SRS ``Return-Path`` rewrite, or as the
      first address of ``X-Forwarded-For: <source> <dest>``.
    - **Manual forward / "forward as attachment"** — the user composed the
      message, so ``From`` *is* their mailbox. This is the fallback.

    A bank's own address never counts as a forwarder: on auto-forwarded mail the
    bank owns both From and (unless Gmail rewrote it) Return-Path, so treating it
    as the forwarder would reject every real bank notification. The SRS match is
    exempt — it is unambiguously Gmail forwarding on someone's behalf.

    **Non-Gmail auto-forward** resolves to None by design. A standard SRS
    Return-Path names the forwarding *domain* and no mailbox, and a domain is
    not an identity — every Hotmail user shares ``outlook.com``, so matching on
    it would buy nothing a spoofed From doesn't already get. Better to say we
    can't tell and fail open than to invent an address and reject on it.

    Returns None when nothing resolves; the caller treats that as "can't tell"
    and lets the message through rather than risk silently dropping real mail.
    """
    for header in _FORWARDER_HEADERS:
        for value in _header_values(payload, header):
            srs = _SRS_RETURN_PATH_RE.search(value)
            if srs:
                return f"{srs.group(1)}@{srs.group(2)}".lower()
            if _SRS_TOKEN_RE.search(value):
                # Skip the value entirely rather than return: a later header may
                # still name the mailbox, and if none does, the From fallback
                # below still catches a *manual* forward from a stranger.
                log.info("%s is SRS-rewritten (%r) — no forwarding mailbox in it",
                         header, value)
                continue
            addr = _ANY_ADDR_RE.search(value)
            if addr and not _is_bank_address(addr.group(0)):
                return addr.group(0).lower()
    addr = _ANY_ADDR_RE.search(from_addr or "")
    if addr and not _is_bank_address(addr.group(0)):
        return addr.group(0).lower()
    return None


def _is_bank_address(address: str) -> bool:
    lowered = (address or "").lower()
    return any(d in lowered for d in KNOWN_BANK_DOMAINS)


def _trusted_forwarders(session: Session, user_id) -> set[str]:
    """Mailboxes allowed to forward into this account: the owner's registered
    email (always, so accounts predating the table keep working) plus any extra
    addresses they added in settings."""
    trusted = set()
    user = session.get(User, user_id)
    if user is not None and user.email:
        trusted.add(user.email.strip().lower())
    trusted.update(
        row.address.strip().lower() for row in session.scalars(
            select(VerifiedForwarder).where(
                VerifiedForwarder.user_id == user_id)).all()
        if row.address)
    return trusted


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
    attachment is counted and skipped, never aborting the rest.

    Attachments that arrive with no content are counted apart from the ones we
    read and rejected. They mean the provider adapter failed to fetch them, not
    that the user forwarded something we don't parse — a distinction that cost a
    day when Resend's empty .eml files were indistinguishable, in the note, from
    mail that simply wasn't from a bank (2026-07-26).
    """
    processed = skipped = empty = 0
    for i, att in enumerate(attachments):
        try:
            data = base64.b64decode(att.get("Content", "") or "")
            if not data:
                log.error("backfill attachment %d of raw_email %s has no content "
                          "(%r) — provider fetch failed", i, raw.id, att.get("Name"))
                empty += 1
                continue
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
    if empty:
        raw.note += f" — {empty} could not be downloaded"


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

    def add(value: object | None, *, anywhere: bool = False) -> None:
        # A repeated header (several Delivered-To on a re-forwarded message)
        # arrives as a list from some providers. Recurse rather than stringify:
        # str(["u_tok@x"]) wraps the address in quotes and the token pattern,
        # which anchors on address delimiters, then fails to see it.
        if isinstance(value, (list, tuple)):
            for item in value:
                add(item, anywhere=anywhere)
            return
        value = str(value or "").strip()
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
        if isinstance(header, dict) and str(header.get("Name") or "").lower() in _ROUTING_HEADERS:
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


def get_or_create_manual_card(session: Session, user_id) -> Card:
    """The "Efectivo / otro" bucket a hand-entered transaction lands on when no
    real card is behind it — cash, or a transfer from an account the app never
    sees mail for. Transaction.card_id is NOT NULL, so a pseudo-card is how
    "no card" is represented without a migration.

    The label is stored in Spanish at creation (like the system categories)
    rather than translated at render time, which keeps Card.display free of
    i18n and lets the user rename it.
    """
    card = _get_or_create_card(session, user_id, Bank.MANUAL.value, MANUAL_LAST4)
    if card.needs_review:  # freshly created — it is not a card to review
        card.needs_review = False
        card.label = MANUAL_CARD_LABEL
        session.flush()
    return card


def dedupe_key_for(card: Card, txn_date: date, merchant: str,
                   signed_amount: float) -> str:
    """The content dedupe key for one transaction, unique per user.

    Keyed on the card's STABLE identity (bank+last4), not its display label —
    a user relabeling a card must not make the same charge look new and
    double-count it. Shared by ingestion and manual entry so that a bank email
    arriving after the user already typed the charge in collapses onto the
    existing row instead of doubling it.
    """
    return "|".join(str(p) for p in signature(
        f"{card.bank}:{card.last4}", txn_date, merchant, signed_amount))


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
