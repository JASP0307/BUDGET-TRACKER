"""Which mailboxes are allowed to forward mail into an account.

REGRESSION (2026-07-26): a month-backfill batch forwarded from a second Gmail
was addressed to the *wrong* inbound address and filed ten transactions onto
another account. Routing had behaved exactly as designed — it routes on the
`u_<token>` address alone, and nothing checked that the mailbox doing the
forwarding owned the account that token points at. Knowing (or guessing) someone
else's inbound address was enough to write into their budget.

Ingest now resolves the forwarding mailbox and requires it to be verified for
the destination account. The deliberate gap, pinned by
`test_unidentifiable_forwarder_is_let_through` below: when no forwarder can be
identified at all the message is let through, because over-strict header logic
already silently broke live ingestion for days once (see test_routing.py).
"""

import base64
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select

from conftest import OWNER_EMAIL

FIXTURES = Path(__file__).parent.parent.parent / "core" / "tests" / "fixtures"

QIK_SENDER = "notificaciones@qik.do"
QIK_SUBJECT = "Usaste tu tarjeta de crédito Qik"
STRANGER = "juanab0307@gmail.com"  # the real second Gmail, from the incident
PMHASH = "32c2ad98da061df4750eb3c0bbf65e71"


def _token(client) -> str:
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    with get_sessionmaker()() as s:
        return s.scalar(select(InboundAddress)).token


def _post(client, payload):
    return client.post("/webhooks/postmark-inbound/testsecret", json=payload)


def _payload(message_id, token, *, sender=QIK_SENDER, headers=None,
             subject=QIK_SUBJECT, html=None, attachments=None):
    payload = {
        "MessageID": message_id,
        "From": sender,
        "Subject": subject,
        "ToFull": [{"Email": f"u_{token}@in.example.do"}],
        "HtmlBody": html if html is not None
                    else (FIXTURES / "qik_purchase.html").read_text(encoding="utf-8"),
        "Headers": headers or [],
    }
    if attachments is not None:
        payload["Attachments"] = attachments
    return payload


def _srs_return_path(mailbox: str, token: str) -> str:
    """Gmail's SRS rewrite of Return-Path when *mailbox* auto-forwards to us."""
    local, domain = mailbox.split("@", 1)
    return (f"<{local}+caf_={PMHASH}+u_{token}"
            f"=inbound.postmarkapp.com@{domain}>")


def _standard_srs_return_path(forwarding_domain: str, sender: str = QIK_SENDER,
                              *, version: int = 0) -> str:
    """Standard SRS — what every forwarder that isn't Gmail emits (Microsoft,
    Postfix, cPanel): the *original sender* re-hosted at the forwarding domain.

    Note what is and isn't in here: the bank's local part and domain are, the
    forwarding mailbox is not. That asymmetry is the whole point — there is no
    address to attribute, only a domain.
    """
    orig_local, orig_domain = sender.split("@", 1)
    return (f"<SRS{version}=HHH=TT={orig_domain}={orig_local}"
            f"@{forwarding_domain}>")


def _raw(message_id):
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        return s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == message_id))


def _transactions():
    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        return s.scalars(select(Transaction)).all()


def _add_verified_forwarder(client, address):
    from web.app.db import get_sessionmaker
    from web.app.models import User, VerifiedForwarder
    with get_sessionmaker()() as s:
        user = s.scalar(select(User))
        s.add(VerifiedForwarder(user_id=user.id, address=address))
        s.commit()


def _eml(fixture, sender, subject, to=OWNER_EMAIL) -> dict:
    """One original bank email packaged the way "forward as attachment" does."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["To"] = to
    msg.set_content("(sin texto)")
    msg.add_alternative((FIXTURES / fixture).read_text(encoding="utf-8"),
                        subtype="html")
    return {"Name": f"{fixture}.eml", "ContentType": "message/rfc822",
            "Content": base64.b64encode(msg.as_bytes()).decode()}


# ---- The incident itself ----

def test_manual_forward_from_another_mailbox_is_rejected(client):
    """The 2026-07-26 case: a real forward, a real bank email inside it, aimed at
    someone else's inbound address. Must not become a transaction."""
    resp = _post(client, _payload("t1", _token(client), sender=STRANGER))
    assert resp.json() == {"status": "rejected"}
    assert _transactions() == []
    assert STRANGER in _raw("t1").note


def test_backfill_batch_from_another_mailbox_is_rejected_whole(client):
    """The incident arrived as a "forward as attachment" batch. The gate sits
    before the attachment loop, so the batch is refused as one unit rather than
    ten times over."""
    payload = _payload("t2", _token(client), sender=STRANGER,
                       subject="Fwd: mis consumos", html="<p>aquí están</p>",
                       attachments=[
                           _eml("qik_purchase.html", QIK_SENDER, QIK_SUBJECT,
                                to=STRANGER),
                           _eml("popular_consumo.html",
                                "notificaciones@popularenlinea.com",
                                "Notificación de Consumo", to=STRANGER)])
    assert _post(client, payload).json() == {"status": "rejected"}
    assert _transactions() == []


# ---- The owner's own mail still gets through ----

def test_backfill_batch_from_the_owner_is_ingested(client):
    """The same batch shape, forwarded by the mailbox that owns the account."""
    payload = _payload("t3", _token(client), sender=OWNER_EMAIL,
                       subject="Fwd: mis consumos", html="<p>aquí están</p>",
                       attachments=[
                           _eml("qik_purchase.html", QIK_SENDER, QIK_SUBJECT),
                           _eml("popular_consumo.html",
                                "notificaciones@popularenlinea.com",
                                "Notificación de Consumo")])
    assert _post(client, payload).json() == {"status": "backfilled"}
    assert len(_transactions()) == 2


def test_gmail_autoforward_from_the_owner_is_ingested(client):
    """Auto-forwarded bank mail: From is the bank, and the owner's mailbox shows
    up only in Gmail's SRS Return-Path."""
    token = _token(client)
    resp = _post(client, _payload("t4", token, headers=[
        {"Name": "Return-Path", "Value": _srs_return_path(OWNER_EMAIL, token)}]))
    assert resp.json() == {"status": "processed"}
    assert len(_transactions()) == 1


def test_gmail_autoforward_from_another_mailbox_is_rejected(client):
    """Same shape, but someone else's Gmail is doing the forwarding."""
    token = _token(client)
    resp = _post(client, _payload("t5", token, headers=[
        {"Name": "Return-Path", "Value": _srs_return_path(STRANGER, token)}]))
    assert resp.json() == {"status": "rejected"}
    assert _transactions() == []


def test_x_forwarded_for_identifies_the_forwarder(client):
    """Gmail also records `X-Forwarded-For: <source> <dest>`; the source is the
    forwarding mailbox."""
    token = _token(client)
    resp = _post(client, _payload("t6", token, headers=[
        {"Name": "X-Forwarded-For",
         "Value": f"{STRANGER} u_{token}@in.example.do"}]))
    assert resp.json() == {"status": "rejected"}
    assert _transactions() == []


# ---- Verified extra mailboxes ----

def test_a_verified_forwarder_is_accepted(client):
    """A second mailbox the user added in settings can forward too."""
    token = _token(client)
    _add_verified_forwarder(client, "work@example.com")
    resp = _post(client, _payload("t7", token, headers=[
        {"Name": "Return-Path",
         "Value": _srs_return_path("work@example.com", token)}]))
    assert resp.json() == {"status": "processed"}
    assert len(_transactions()) == 1


def test_a_verified_forwarder_belonging_to_another_user_is_not(client):
    """Verification is per-account: user B trusting an address must not let that
    address write into user A."""
    from web.app.auth.service import register_user
    from web.app.db import get_sessionmaker
    from web.app.models import VerifiedForwarder
    with get_sessionmaker()() as s:
        other = register_user(s, "other@example.com", "supersecret")
        s.flush()
        s.add(VerifiedForwarder(user_id=other.id, address=STRANGER))
        s.commit()

    resp = _post(client, _payload("t8", _token(client), sender=STRANGER))
    assert resp.json() == {"status": "rejected"}
    assert _transactions() == []


def test_forwarder_matching_ignores_case(client):
    token = _token(client)
    resp = _post(client, _payload("t9", token, headers=[
        {"Name": "Return-Path",
         "Value": _srs_return_path(OWNER_EMAIL.upper(), token)}]))
    assert resp.json() == {"status": "processed"}


# ---- The deliberate gap, and what must not regress ----

def test_unidentifiable_forwarder_is_let_through(client):
    """Bank mail with no forwarding evidence at all: the bank owns From and
    Return-Path, so no human forwarder resolves. Let through on purpose — being
    strict here is what silently broke live ingestion for days. Closing this is
    the DMARC/SPF follow-up, not this gate.
    """
    resp = _post(client, _payload("t10", _token(client), headers=[
        {"Name": "Return-Path", "Value": f"<{QIK_SENDER}>"}]))
    assert resp.json() == {"status": "processed"}
    assert len(_transactions()) == 1


# ---- Providers that aren't Gmail ----

def test_standard_srs_autoforward_is_ingested(client):
    """REGRESSION (2026-08-02, first Hotmail signup): a standard `SRS0=`
    Return-Path used to be picked apart by the generic address fallback, which
    matched the tail of the SRS token — `notificaciones@outlook.com` for a Qik
    email — called that the forwarding mailbox, found it in nobody's trusted
    set, and rejected every real transaction the user had.

    An SRS token names a forwarding *domain* and no mailbox, so the honest
    answer is "can't tell", which lands on the fail-open path below.
    """
    resp = _post(client, _payload("t12", _token(client), headers=[
        {"Name": "Return-Path", "Value": _standard_srs_return_path("outlook.com")}]))
    assert resp.json() == {"status": "processed"}
    assert len(_transactions()) == 1


def test_standard_srs_is_not_read_as_a_forwarding_mailbox(client):
    """The specific misreading, pinned directly: nothing derived from the SRS
    token may end up in the rejection note."""
    _post(client, _payload("t13", _token(client), headers=[
        {"Name": "Return-Path", "Value": _standard_srs_return_path("outlook.com")}]))
    raw = _raw("t13")
    assert raw.processing_status == "processed"
    assert "outlook.com" not in (raw.note or "")


def test_srs1_chained_forward_is_ingested(client):
    """A second hop rewrites SRS0 to SRS1; same reasoning, same outcome."""
    resp = _post(client, _payload("t14", _token(client), headers=[
        {"Name": "Return-Path",
         "Value": _standard_srs_return_path("hotmail.com", version=1)}]))
    assert resp.json() == {"status": "processed"}


def test_standard_srs_does_not_excuse_a_manual_forward_from_a_stranger(client):
    """Failing open on the SRS *header* must not weaken the From fallback: a
    stranger manually forwarding still owns From, and is still refused. This is
    what keeps the 2026-07-26 incident closed for non-Gmail providers."""
    resp = _post(client, _payload("t15", _token(client), sender=STRANGER, headers=[
        {"Name": "Return-Path", "Value": _standard_srs_return_path("outlook.com")}]))
    assert resp.json() == {"status": "rejected"}
    assert _transactions() == []


def test_a_later_header_still_names_the_forwarder(client):
    """An unreadable Return-Path skips that value rather than abandoning the
    search — X-Forwarded-For is still consulted, and still rejects a stranger."""
    token = _token(client)
    resp = _post(client, _payload("t16", token, headers=[
        {"Name": "Return-Path", "Value": _standard_srs_return_path("outlook.com")},
        {"Name": "X-Forwarded-For",
         "Value": f"{STRANGER} u_{token}@in.example.do"}]))
    assert resp.json() == {"status": "rejected"}
    assert _transactions() == []


def test_gmail_srs_still_identifies_the_forwarder(client):
    """The new SRS branch must not shadow Gmail's `+caf_=` form, which *does*
    name the mailbox and must keep rejecting a stranger's auto-forward."""
    token = _token(client)
    assert _post(client, _payload("t17", token, headers=[
        {"Name": "Return-Path", "Value": _srs_return_path(STRANGER, token)}]
    )).json() == {"status": "rejected"}


def test_gmail_forwarding_confirmation_is_never_rejected(client):
    """Onboarding mail legitimately comes from Google, not the user's mailbox.
    If the gate ran first, nobody could ever finish setting up forwarding."""
    resp = _post(client, _payload(
        "t11", _token(client), sender="forwarding-noreply@google.com",
        subject="(#123456789) Confirmación de reenvío de Gmail",
        html="<p>código 123456789</p>"))
    assert resp.json() == {"status": "confirmation"}
    assert _raw("t11").note == "123456789"
