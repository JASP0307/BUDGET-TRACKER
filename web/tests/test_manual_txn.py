"""Transactions typed in by hand — spend no bank emailed about.

The contract these tests pin down: a manual row is indistinguishable from an
ingested one (same rules, same content dedupe key, same shape), it is the only
kind that can be edited or deleted, and it never silently doubles a month.
"""

from datetime import date
from pathlib import Path

from sqlalchemy import func, select

FIXTURES = Path(__file__).parent.parent.parent / "core" / "tests" / "fixtures"

# The qik_purchase.html fixture, as budgetcore parses it. A manual entry with
# these exact values is the "I typed it in before the email arrived" case.
QIK_MERCHANT = "AMAZON 1"
QIK_AMOUNT = 2031.75
QIK_DATE = "2025-07-15"


def _uid(email):
    from web.app.db import get_sessionmaker
    from web.app.models import User
    with get_sessionmaker()() as s:
        return s.scalar(select(User).where(func.lower(User.email) == email.lower())).id


def _signup_login(client, email, pw="supersecret"):
    from web.app.auth.security import make_verify_token
    client.post("/register", data={"email": email, "password": pw},
                follow_redirects=True)
    uid = _uid(email)
    client.get(f"/verify?token={make_verify_token(uid)}", follow_redirects=True)
    client.post("/login", data={"email": email, "password": pw})
    return uid


def _cat(uid, name):
    from web.app.db import get_sessionmaker
    from web.app.models import Category
    with get_sessionmaker()() as s:
        return s.scalar(select(Category).where(Category.user_id == uid,
                                               Category.name == name))


def _card(uid, bank="qik", last4="3333"):
    """A registered card, the way onboarding creates one."""
    from web.app.db import get_sessionmaker
    from web.app.models import Card
    with get_sessionmaker()() as s:
        card = Card(user_id=uid, bank=bank, last4=last4)
        s.add(card)
        s.commit()
        return card.id


def _txns(uid):
    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        return s.scalars(select(Transaction)
                         .where(Transaction.user_id == uid)
                         .order_by(Transaction.created_at)).all()


def _categories(uid):
    """Category names of a user's transactions, read inside a session — the
    rows _txns hands back are detached and can't lazy-load the relationship."""
    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        return [t.category.name if t.category else None
                for t in s.scalars(select(Transaction)
                                   .where(Transaction.user_id == uid)
                                   .order_by(Transaction.created_at))]


def _form(**over):
    form = {"merchant": "JUAN PEREZ", "txn_date": date.today().isoformat(),
            "original_amount": "1500", "currency": "RD$", "tx_type": "consumo",
            "card": "manual", "category_id": ""}
    form.update(over)
    return form


def _add(client, **over):
    return client.post("/transactions/new", data=_form(**over),
                       follow_redirects=False)


# --- creating ---------------------------------------------------------------

def test_creates_a_transaction_marked_manual(client):
    uid = _signup_login(client, "man1@example.com")

    r = _add(client, merchant="  Juan Pérez  ", original_amount="1,500.50")

    assert r.status_code == 303
    txn, = _txns(uid)
    assert txn.merchant == "Juan Pérez"          # trimmed, not upper-cased
    assert txn.amount_dop == 1500.50             # thousands separator tolerated
    assert txn.original_amount == 1500.50
    assert txn.currency == "RD$"
    assert txn.fx_rate_used == 1.0
    assert txn.raw_email_id is None              # the mark of a manual row


def test_manual_card_is_created_once_and_reused(client):
    from budgetcore.models import Bank
    from web.app.db import get_sessionmaker
    from web.app.models import Card
    uid = _signup_login(client, "man2@example.com")

    _add(client, merchant="JUAN")
    _add(client, merchant="MARIA")

    with get_sessionmaker()() as s:
        cards = s.scalars(select(Card).where(
            Card.user_id == uid, Card.bank == Bank.MANUAL.value)).all()
        assert len(cards) == 1
        assert cards[0].display == "Efectivo / otro"
        assert not cards[0].needs_review  # nothing to review about the bucket
    assert {t.card_id for t in _txns(uid)} == {cards[0].id}


def test_registered_card_can_be_picked(client):
    uid = _signup_login(client, "man3@example.com")
    card_id = _card(uid)

    _add(client, card=str(card_id))

    txn, = _txns(uid)
    assert txn.card_id == card_id


def test_blank_category_applies_the_users_rules(client):
    uid = _signup_login(client, "man4@example.com")
    delivery = _cat(uid, "Delivery")
    client.post("/rules", data={"substring": "uber eats",
                                "category_id": str(delivery.id)})

    _add(client, merchant="UBER EATS DO", category_id="")

    assert _categories(uid) == ["Delivery"]


def test_unmatched_merchant_falls_back_to_uncategorized(client):
    uid = _signup_login(client, "man5@example.com")

    _add(client, merchant="ALGO QUE NADIE CONOCE")

    assert _categories(uid) == ["Otros / sin categoría"]


def test_explicit_category_wins_over_the_rules(client):
    uid = _signup_login(client, "man6@example.com")
    delivery = _cat(uid, "Delivery")
    salidas = _cat(uid, "Salidas")
    client.post("/rules", data={"substring": "uber",
                                "category_id": str(delivery.id)})

    _add(client, merchant="UBER EATS", category_id=str(salidas.id))

    assert _categories(uid) == ["Salidas"]


def test_withdrawal_lands_in_retiro_efectivo(client):
    uid = _signup_login(client, "man7@example.com")

    _add(client, merchant="CAJERO ESQUINA", tx_type="retiro")

    txn, = _txns(uid)
    assert txn.tx_type == "retiro"
    assert _categories(uid) == ["Retiro Efectivo"]


def test_reversal_is_stored_negative(client):
    """A refund has to subtract, the same way an emailed reversal does."""
    uid = _signup_login(client, "man8@example.com")

    _add(client, merchant="DEVOLUCION TIENDA", tx_type="reversal",
         original_amount="800")

    txn, = _txns(uid)
    assert txn.amount_dop == -800.0
    assert txn.original_amount == 800.0


def test_usd_is_converted_at_the_current_rate(client):
    from web.app.db import get_sessionmaker
    from web.app.models import FxRate
    uid = _signup_login(client, "man9@example.com")
    with get_sessionmaker()() as s:
        s.add(FxRate(pair="USD/DOP", rate=62.5, effective_date=date.today()))
        s.commit()

    _add(client, merchant="STEAM", currency="US$", original_amount="10")

    txn, = _txns(uid)
    assert txn.original_amount == 10.0
    assert txn.currency == "US$"
    assert txn.fx_rate_used == 62.5
    assert txn.amount_dop == 625.0


# --- notification -----------------------------------------------------------

def test_adding_sends_the_same_alert_the_email_path_sends(monkeypatch, client):
    from web.app.db import get_sessionmaker
    from web.app.models import Budget
    from web.app.services import notify, telegram
    uid = _signup_login(client, "man27@example.com")
    delivery = _cat(uid, "Delivery")
    with get_sessionmaker()() as s:
        notify.set_telegram_chat(s, uid, "555")
        s.add(Budget(user_id=uid, category_id=delivery.id,
                     month=date.today().replace(day=1), amount_dop=5000))
        s.commit()
    sent = []
    monkeypatch.setattr(telegram, "send_message",
                        lambda chat_id, text: sent.append((chat_id, text)))

    _add(client, merchant="PIZZA", original_amount="1500",
         category_id=str(delivery.id))

    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "555"
    assert "PIZZA" in text
    assert "Efectivo / otro" in text          # the card line
    # The remaining-budget line is the reason to send it at all.
    assert "3,500.00" in text


def test_a_telegram_outage_does_not_lose_the_transaction(monkeypatch, client):
    from web.app.db import get_sessionmaker
    from web.app.services import notify, telegram
    uid = _signup_login(client, "man28@example.com")
    with get_sessionmaker()() as s:
        notify.set_telegram_chat(s, uid, "555")
        s.commit()

    def boom(chat_id, text):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(telegram, "send_message", boom)

    r = _add(client, merchant="PANADERIA")

    assert r.status_code == 303
    assert len(_txns(uid)) == 1


def test_editing_does_not_re_alert(client, monkeypatch):
    """The alert answers "you just spent this"; a correction is not that."""
    from web.app.db import get_sessionmaker
    from web.app.services import notify, telegram
    uid = _signup_login(client, "man29@example.com")
    with get_sessionmaker()() as s:
        notify.set_telegram_chat(s, uid, "555")
        s.commit()
    _add(client, merchant="PANADERIA")
    txn, = _txns(uid)
    sent = []
    monkeypatch.setattr(telegram, "send_message",
                        lambda chat_id, text: sent.append(text))

    client.post(f"/transactions/{txn.id}/edit",
                data=_form(merchant="PANADERIA", original_amount="99"))

    assert sent == []


# --- validation -------------------------------------------------------------

def test_bad_input_re_renders_the_form_with_what_was_typed(client):
    uid = _signup_login(client, "man10@example.com")

    r = _add(client, original_amount="abc", merchant="PANADERIA")

    assert r.status_code == 200                  # the form, not a redirect
    assert "Monto inválido." in r.text
    assert 'value="PANADERIA"' in r.text         # nothing retyped
    assert _txns(uid) == []


def test_zero_amount_is_refused(client):
    uid = _signup_login(client, "man11@example.com")
    assert "mayor que cero" in _add(client, original_amount="0").text
    assert _txns(uid) == []


def test_another_users_card_is_refused(client):
    uid_a = _signup_login(client, "man12a@example.com")
    card_a = _card(uid_a)
    client.post("/logout")
    uid_b = _signup_login(client, "man12b@example.com")

    r = _add(client, card=str(card_a))

    assert r.status_code == 200
    assert "Elige una cuenta o tarjeta." in r.text
    assert _txns(uid_b) == [] and _txns(uid_a) == []


def test_another_users_category_is_refused(client):
    uid_a = _signup_login(client, "man13a@example.com")
    cat_a = _cat(uid_a, "Delivery")
    client.post("/logout")
    uid_b = _signup_login(client, "man13b@example.com")

    r = _add(client, category_id=str(cat_a.id))

    assert "Categoría inválida." in r.text
    assert _txns(uid_b) == []


# --- duplicates -------------------------------------------------------------

def test_identical_entry_asks_before_doubling_the_month(client):
    uid = _signup_login(client, "man14@example.com")
    _add(client, merchant="JUAN PEREZ", original_amount="1500")

    r = _add(client, merchant="JUAN PEREZ", original_amount="1500")

    assert r.status_code == 200
    assert "Ya tienes una transacción idéntica" in r.text
    assert 'name="separate"' in r.text            # the confirm checkbox appears
    assert len(_txns(uid)) == 1


def test_confirming_it_is_separate_saves_it(client):
    uid = _signup_login(client, "man15@example.com")
    _add(client, merchant="JUAN PEREZ", original_amount="1500")

    r = client.post("/transactions/new",
                    data=_form(merchant="JUAN PEREZ", original_amount="1500",
                               separate="1"), follow_redirects=False)

    assert r.status_code == 303
    first, second = _txns(uid)
    assert first.dedupe_key != second.dedupe_key   # salted, so both survive
    assert second.dedupe_key.startswith(first.dedupe_key)


def test_a_later_bank_email_collapses_onto_the_manual_row(client):
    """The point of sharing the dedupe key: typing a charge in before the bank
    emails about it must not end up counting the charge twice."""
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    uid = _signup_login(client, "man16@example.com")
    card_id = _card(uid, bank="qik", last4="3333")
    _add(client, card=str(card_id), merchant=QIK_MERCHANT,
         txn_date=QIK_DATE, original_amount=str(QIK_AMOUNT))

    with get_sessionmaker()() as s:
        token = s.scalar(select(InboundAddress).where(
            InboundAddress.user_id == uid)).token
    resp = client.post("/webhooks/postmark-inbound/testsecret", json={
        "MessageID": "late1", "From": "notificaciones@qik.do",
        "Subject": "Usaste tu tarjeta de crédito Qik",
        "ToFull": [{"Email": f"u_{token}@in.example.do"}],
        "HtmlBody": (FIXTURES / "qik_purchase.html").read_text(encoding="utf-8"),
        "Headers": []})

    assert resp.json()["status"] == "skipped"
    assert len(_txns(uid)) == 1


# --- editing ----------------------------------------------------------------

def test_edit_page_is_prefilled(client):
    uid = _signup_login(client, "man17@example.com")
    _add(client, merchant="PANADERIA", original_amount="250")
    txn, = _txns(uid)

    body = client.get(f"/transactions/{txn.id}/edit").text

    assert 'value="PANADERIA"' in body
    assert 'value="250.00"' in body
    assert f'action="/transactions/{txn.id}/edit"' in body


def test_edit_updates_the_row_and_its_dedupe_key(client):
    uid = _signup_login(client, "man18@example.com")
    _add(client, merchant="PANADERIA", original_amount="250")
    before, = _txns(uid)

    r = client.post(f"/transactions/{before.id}/edit",
                    data=_form(merchant="PANADERIA LA ROSA",
                               original_amount="275.50"),
                    follow_redirects=False)

    assert r.status_code == 303
    after, = _txns(uid)
    assert after.id == before.id
    assert (after.merchant, after.amount_dop) == ("PANADERIA LA ROSA", 275.50)
    assert after.dedupe_key != before.dedupe_key


def test_edit_into_an_existing_row_asks_before_saving(client):
    uid = _signup_login(client, "man19@example.com")
    _add(client, merchant="PANADERIA", original_amount="250")
    _add(client, merchant="COLMADO", original_amount="900")
    _, second = _txns(uid)

    r = client.post(f"/transactions/{second.id}/edit",
                    data=_form(merchant="PANADERIA", original_amount="250"),
                    follow_redirects=False)

    assert r.status_code == 200
    assert "Ya tienes una transacción idéntica" in r.text
    assert _txns(uid)[1].merchant == "COLMADO"  # untouched


def test_editing_an_emailed_transaction_is_refused(client):
    """An emailed row is what the bank said; it must not be rewritten."""
    uid = _signup_login(client, "man20@example.com")
    _emailed_txn(uid, "k-email")
    txn, = _txns(uid)

    assert client.get(f"/transactions/{txn.id}/edit",
                      follow_redirects=False).status_code == 303
    r = client.post(f"/transactions/{txn.id}/edit",
                    data=_form(merchant="OTRA COSA"), follow_redirects=False)

    assert r.status_code == 303
    assert _txns(uid)[0].merchant == "AMAZON"


def test_cannot_edit_another_users_transaction(client):
    uid_a = _signup_login(client, "man21a@example.com")
    _add(client, merchant="PANADERIA")
    txn, = _txns(uid_a)
    client.post("/logout")
    _signup_login(client, "man21b@example.com")

    r = client.post(f"/transactions/{txn.id}/edit",
                    data=_form(merchant="ROBADO"), follow_redirects=False)

    assert r.status_code == 303
    assert _txns(uid_a)[0].merchant == "PANADERIA"


# --- deleting ---------------------------------------------------------------

def test_delete_removes_the_row_and_its_suggestion(client):
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestion
    uid = _signup_login(client, "man22@example.com")
    _add(client, merchant="ALGO RARO")
    txn, = _txns(uid)
    delivery = _cat(uid, "Delivery")
    with get_sessionmaker()() as s:
        s.add(CategorySuggestion(user_id=uid, transaction_id=txn.id,
                                 category_id=delivery.id, model="test"))
        s.commit()

    r = client.post(f"/transactions/{txn.id}/delete", follow_redirects=False)

    assert r.status_code == 303
    assert _txns(uid) == []
    with get_sessionmaker()() as s:
        assert s.scalars(select(CategorySuggestion)).all() == []


def test_deleting_an_emailed_transaction_is_refused(client):
    uid = _signup_login(client, "man23@example.com")
    _emailed_txn(uid, "k-email2")
    txn, = _txns(uid)

    r = client.post(f"/transactions/{txn.id}/delete", follow_redirects=False)

    assert r.status_code == 303
    assert len(_txns(uid)) == 1


def test_cannot_delete_another_users_transaction(client):
    uid_a = _signup_login(client, "man24a@example.com")
    _add(client, merchant="PANADERIA")
    txn, = _txns(uid_a)
    client.post("/logout")
    _signup_login(client, "man24b@example.com")

    assert client.post(f"/transactions/{txn.id}/delete",
                       follow_redirects=False).status_code == 303
    assert len(_txns(uid_a)) == 1


# --- entry points -----------------------------------------------------------

def test_dashboard_links_to_the_form_and_only_edits_manual_rows(client):
    uid = _signup_login(client, "man25@example.com")
    _emailed_txn(uid, "k-email3")
    _add(client, merchant="PANADERIA")
    emailed, manual = sorted(_txns(uid), key=lambda t: t.merchant)

    body = client.get("/").text

    assert 'href="/transactions/new"' in body
    assert f'href="/transactions/{manual.id}/edit"' in body
    assert f'href="/transactions/{emailed.id}/edit"' not in body


def test_manual_bucket_is_hidden_from_the_setup_card_list(client):
    _signup_login(client, "man26@example.com")
    _add(client, merchant="PANADERIA")

    body = client.get("/setup").text

    assert "Efectivo / otro" not in body
    assert "Manual ·0000" not in body


def _emailed_txn(uid, dedupe_key):
    """A transaction that arrived by email — the kind manual edit/delete must
    keep its hands off. `raw_email_id` set is the whole point."""
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail, Transaction
    card_id = _card(uid)
    with get_sessionmaker()() as s:
        raw = RawEmail(user_id=uid, provider_message_id=f"pm-{dedupe_key}",
                       from_addr="notificaciones@qik.do", subject="x",
                       processing_status="processed")
        s.add(raw)
        s.flush()
        txn = Transaction(user_id=uid, raw_email_id=raw.id, card_id=card_id,
                          tx_type="consumo", merchant="AMAZON",
                          txn_date=date.today(), original_amount=100,
                          currency="RD$", amount_dop=100, dedupe_key=dedupe_key)
        s.add(txn)
        s.commit()
        return txn.id
