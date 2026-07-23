"""Webhook → pipeline tests using the real bank-email fixtures."""

from pathlib import Path

from sqlalchemy import select

FIXTURES = Path(__file__).parent.parent.parent / "core" / "tests" / "fixtures"

QIK_SENDER = "notificaciones@qik.do"
QIK_SUBJECT = "Usaste tu tarjeta de crédito Qik"


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
    resp = _post(client, _payload("m4", _token(client),
                                  sender="attacker@evil.example",
                                  html="<html>fake purchase RD$9,999.00</html>"))
    assert resp.json()["status"] == "skipped"

    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        assert s.scalar(select(Transaction)) is None


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
