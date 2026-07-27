"""Webhook → pipeline tests using the real bank-email fixtures."""

import base64
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select

from conftest import OWNER_EMAIL

FIXTURES = Path(__file__).parent.parent.parent / "core" / "tests" / "fixtures"

QIK_SENDER = "notificaciones@qik.do"
QIK_SUBJECT = "Usaste tu tarjeta de crédito Qik"
POPULAR_SENDER = "notificaciones@popularenlinea.com"
POPULAR_SUBJECT = "Notificación de Consumo"


def _payload(message_id: str, to_token: str, sender=QIK_SENDER,
             subject=QIK_SUBJECT, fixture="qik_purchase.html", html=None):
    return {
        "MessageID": message_id,
        "From": sender,
        "Subject": subject,
        "ToFull": [{"Email": f"u_{to_token}@in.example.do"}],
        "HtmlBody": html if html is not None
                    else (FIXTURES / fixture).read_text(encoding="utf-8"),
        "Headers": [],
    }


def _token(client) -> str:
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    with get_sessionmaker()() as s:
        return s.scalar(select(InboundAddress)).token


def _post(client, payload, secret="testsecret"):
    return client.post(f"/webhooks/postmark-inbound/{secret}", json=payload)


def test_wrong_secret_is_404(client):
    assert _post(client, _payload("m0", "whatever"), secret="bad").status_code == 404


def test_qik_purchase_end_to_end(client):
    resp = _post(client, _payload("m1", _token(client)))
    assert resp.status_code == 200
    assert resp.json() == {"status": "processed"}

    from web.app.db import get_sessionmaker
    from web.app.models import Card, Transaction
    with get_sessionmaker()() as s:
        txn = s.scalar(select(Transaction))
        assert txn.merchant == "AMAZON 1"
        assert txn.amount_dop == 2031.75
        assert txn.category.name == "Otros / sin categoría"  # no rules yet
        card = s.get(Card, txn.card_id)
        assert (card.bank, card.last4) == ("qik", "3333")
        assert card.needs_review  # auto-created, unlabeled


def test_dedupe_survives_card_relabel(client):
    """The same charge must dedupe even after the card is relabeled — the key
    is on bank+last4, not the mutable display label."""
    from web.app.db import get_sessionmaker
    from web.app.models import Card, Transaction
    token = _token(client)

    assert _post(client, _payload("r1", token)).json()["status"] == "processed"
    with get_sessionmaker()() as s:
        card = s.scalar(select(Card))
        card.label = "Mi Qik Renombrada"
        s.commit()
    # Same charge, new message id (bank double-send) after relabel → still a dup.
    assert _post(client, _payload("r2", token)).json()["status"] == "skipped"
    with get_sessionmaker()() as s:
        assert len(s.scalars(select(Transaction)).all()) == 1


def test_postmark_mailbox_hash_routes(client):
    """Postmark's default inbound delivers the token as a mailbox hash
    (<pmhash>+u_<token>@inbound.postmarkapp.com), not a u_<token>@ local part."""
    token = _token(client)
    payload = {
        "MessageID": "pm1",
        "From": QIK_SENDER,
        "Subject": QIK_SUBJECT,
        "ToFull": [{"Email": f"abc123hash+u_{token}@inbound.postmarkapp.com",
                    "MailboxHash": f"u_{token}"}],
        "MailboxHash": f"u_{token}",
        "HtmlBody": (FIXTURES / "qik_purchase.html").read_text(encoding="utf-8"),
        "Headers": [],
    }
    assert _post(client, payload).json() == {"status": "processed"}


def test_duplicate_content_is_skipped(client):
    token = _token(client)
    assert _post(client, _payload("m1", token)).json()["status"] == "processed"
    # Same charge, different provider message id — the bank double-send case.
    assert _post(client, _payload("m2", token)).json()["status"] == "skipped"

    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        assert len(s.scalars(select(Transaction)).all()) == 1


def test_webhook_retry_same_message_id_is_idempotent(client):
    token = _token(client)
    _post(client, _payload("m1", token))
    _post(client, _payload("m1", token))
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        assert len(s.scalars(select(RawEmail)).all()) == 1


def test_unknown_token_is_unrecognized(client):
    assert _post(client, _payload("m3", "nosuchtoken1")).json()["status"] == "unrecognized"


def test_non_bank_sender_is_rejected(client):
    """A stranger aiming at a known token is stopped by the trust gate, one step
    before the spoofing guard would have seen the sender isn't a bank."""
    resp = _post(client, _payload("m4", _token(client),
                                  sender="attacker@evil.example",
                                  html="<html>fake purchase RD$9,999.00</html>"))
    assert resp.json()["status"] == "rejected"

    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)) is None


def test_spoofing_guard_still_runs_for_a_trusted_forwarder(client):
    """The bank check is not made redundant by the trust gate: the owner can
    forward whatever they like, and only real bank mail becomes a transaction."""
    resp = _post(client, _payload("m4b", _token(client), sender=OWNER_EMAIL,
                                  html="<html>fake purchase RD$9,999.00</html>"))
    assert resp.json()["status"] == "skipped"

    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail, Transaction
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)) is None
        raw = s.scalar(select(RawEmail).where(
            RawEmail.provider_message_id == "m4b"))
        assert raw.note.startswith("sender not a known bank")


def test_gmail_forwarding_confirmation_surfaces_code(client):
    resp = _post(client, _payload(
        "m5", _token(client), sender="forwarding-noreply@google.com",
        subject="(#123456789) Confirmación de reenvío de Gmail",
        html="<html>Código de confirmación: 123456789</html>"))
    assert resp.json()["status"] == "confirmation"

    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        raw = s.scalar(select(RawEmail))
        assert raw.note == "123456789"


def test_gmail_confirmation_surfaces_link(client):
    """Modern Gmail sends a click-to-confirm (vf-) link and no numeric code —
    the link is what gets stored and surfaced."""
    link = ("https://mail.google.com/mail/vf-%5BANGjdJ9hIs3gYB76H8C"
            "Ox0Hpl3B3SQH7L4RaGu27FMIjWYZ%5D-8sTdzE5gR0QN6zw20qnISrf73Ak")
    resp = _post(client, _payload(
        "m7", _token(client), sender="forwarding-noreply@google.com",
        subject="(Gmail Forwarding Confirmation - Receive Mail from x@gmail.com",
        html=f'<html>please click the link below to confirm: {link} '
             f'to cancel: https://mail.google.com/mail/uf-%5Bxyz%5D-abc</html>'))
    assert resp.json()["status"] == "confirmation"

    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        raw = s.scalar(select(RawEmail))
        assert raw.note == link  # the vf- confirm link, not the uf- cancel one


def test_rule_applies_to_new_transactions(client):
    from web.app.db import get_sessionmaker
    from web.app.models import Category, Rule, User
    with get_sessionmaker()() as s:
        user = s.scalar(select(User))
        target = s.scalar(select(Category).where(Category.name == "Delivery"))
        s.add(Rule(user_id=user.id, substring="AMAZON", category_id=target.id))
        s.commit()

    _post(client, _payload("m6", _token(client)))
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)).category.name == "Delivery"


def test_retro_apply_recategorizes_existing_uncategorized(client):
    # Ingest first, then add the rule — the charge lands as uncategorized.
    _post(client, _payload("m1", _token(client)))
    from web.app.db import get_sessionmaker
    from web.app.models import Category, Rule, Transaction, User
    from web.app.services.ingest import recategorize_uncategorized
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)).category.name == "Otros / sin categoría"
        user = s.scalar(select(User))
        target = s.scalar(select(Category).where(Category.name == "Delivery"))
        s.add(Rule(user_id=user.id, substring="AMAZON", category_id=target.id))
        s.flush()
        assert recategorize_uncategorized(s, user.id) == 1
        s.commit()
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)).category.name == "Delivery"


def test_retro_apply_leaves_manually_categorized_alone(client):
    _post(client, _payload("m1", _token(client)))
    from web.app.db import get_sessionmaker
    from web.app.models import Category, Rule, Transaction, User
    from web.app.services.ingest import recategorize_uncategorized
    with get_sessionmaker()() as s:
        user = s.scalar(select(User))
        salidas = s.scalar(select(Category).where(Category.name == "Salidas"))
        delivery = s.scalar(select(Category).where(Category.name == "Delivery"))
        # User hand-filed this AMAZON charge under Salidas.
        s.scalar(select(Transaction)).category_id = salidas.id
        s.add(Rule(user_id=user.id, substring="AMAZON", category_id=delivery.id))
        s.flush()
        # A matching rule must not override the manual choice.
        assert recategorize_uncategorized(s, user.id) == 0
        s.commit()
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)).category.name == "Salidas"


# ---- Backfill: "Forward as attachment" (message/rfc822 attachments) ----

def _eml_attachment(sender: str, subject: str, fixture: str) -> dict:
    """A Postmark attachment carrying one original bank email as an .eml, the
    way Gmail's "Forward as attachment" packages a selected message."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["To"] = OWNER_EMAIL
    msg.set_content("(sin texto)")
    msg.add_alternative((FIXTURES / fixture).read_text(encoding="utf-8"),
                        subtype="html")
    return {"Name": f"{fixture}.eml", "ContentType": "message/rfc822",
            "Content": base64.b64encode(msg.as_bytes()).decode()}


def _backfill_payload(message_id: str, to_token: str, attachments: list) -> dict:
    return {
        "MessageID": message_id,
        "From": OWNER_EMAIL,  # the user's own forward, not a bank
        "Subject": "Fwd: mis consumos",
        "ToFull": [{"Email": f"u_{to_token}@in.example.do"}],
        "HtmlBody": "<p>aquí están</p>",
        "Attachments": attachments,
        "Headers": [],
    }


def test_backfill_forward_as_attachment(client):
    token = _token(client)
    payload = _backfill_payload("bf1", token, [
        _eml_attachment(QIK_SENDER, QIK_SUBJECT, "qik_purchase.html"),
        _eml_attachment(POPULAR_SENDER, POPULAR_SUBJECT, "popular_consumo.html"),
    ])
    assert _post(client, payload).json() == {"status": "backfilled"}

    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail, Transaction
    with get_sessionmaker()() as s:
        txns = s.scalars(select(Transaction)).all()
        assert {t.merchant for t in txns} == {"AMAZON 1", "SUPERMERCADO EJEMPLO"}
        # All transactions in the batch are archived under the one outer email.
        raw = s.scalar(select(RawEmail).where(RawEmail.provider_message_id == "bf1"))
        assert raw.processing_status == "backfilled"
        assert "backfilled 2 transactions" in raw.note
        assert all(t.raw_email_id == raw.id for t in txns)


def test_backfill_does_not_double_count_live_charge(client):
    """A charge already ingested live must not be re-counted when the user later
    backfills the same email — content dedupe spans both paths."""
    token = _token(client)
    assert _post(client, _payload("live1", token)).json()["status"] == "processed"
    payload = _backfill_payload("bf2", token, [
        _eml_attachment(QIK_SENDER, QIK_SUBJECT, "qik_purchase.html")])
    assert _post(client, payload).json() == {"status": "backfilled"}

    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail, Transaction
    with get_sessionmaker()() as s:
        assert len(s.scalars(select(Transaction)).all()) == 1
        raw = s.scalar(select(RawEmail).where(RawEmail.provider_message_id == "bf2"))
        assert "backfilled 0 transactions (1 skipped)" in raw.note


def test_backfill_names_attachments_that_never_downloaded(client):
    """A contentless attachment is the provider adapter failing, not the user
    forwarding junk, and the note has to say so: when Resend's .eml files came
    back empty the batch read "0 transactions (12 skipped)", which is exactly
    what a batch of non-bank mail looks like, and the real fault stayed hidden."""
    token = _token(client)
    payload = _backfill_payload("bf4", token, [
        {"Name": "Consumo.eml", "ContentType": "message/rfc822", "Content": ""},
        _eml_attachment(QIK_SENDER, QIK_SUBJECT, "qik_purchase.html"),
    ])
    assert _post(client, payload).json() == {"status": "backfilled"}

    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        raw = s.scalar(select(RawEmail).where(RawEmail.provider_message_id == "bf4"))
        assert raw.note == ("backfilled 1 transactions (0 skipped)"
                            " — 1 could not be downloaded")


def test_backfill_skips_non_bank_attachment(client):
    """The bank-domain guard applies per attachment, so a spoofed .eml inside a
    backfill batch is dropped just like a spoofed live email."""
    token = _token(client)
    payload = _backfill_payload("bf3", token, [
        _eml_attachment("attacker@evil.example", "Compra", "qik_purchase.html")])
    assert _post(client, payload).json() == {"status": "backfilled"}

    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)) is None
