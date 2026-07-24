"""User-managed categories: create, de-dupe, protected system rows, safe delete."""

from datetime import date

from sqlalchemy import func, select


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


def test_create_category(client):
    uid = _signup_login(client, "cat1@example.com")
    client.post("/categories", data={"name": "Supermercado"})
    assert _cat(uid, "Supermercado") is not None


def test_create_is_case_insensitively_deduped(client):
    uid = _signup_login(client, "cat2@example.com")
    client.post("/categories", data={"name": "Renta"})   # already a default
    client.post("/categories", data={"name": "renta"})
    from web.app.db import get_sessionmaker
    from web.app.models import Category
    with get_sessionmaker()() as s:
        n = s.scalar(select(func.count()).select_from(Category).where(
            Category.user_id == uid, func.lower(Category.name) == "renta"))
        assert n == 1


def test_blank_name_is_ignored(client):
    uid = _signup_login(client, "cat3@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import Category
    with get_sessionmaker()() as s:
        before = s.scalar(select(func.count()).select_from(Category)
                          .where(Category.user_id == uid))
    client.post("/categories", data={"name": "   "})
    with get_sessionmaker()() as s:
        after = s.scalar(select(func.count()).select_from(Category)
                         .where(Category.user_id == uid))
    assert before == after


def test_delete_reassigns_txns_and_drops_rules_and_budgets(client):
    from web.app.db import get_sessionmaker
    from web.app.models import Budget, Card, Category, Rule, Transaction
    uid = _signup_login(client, "cat4@example.com")
    delivery = _cat(uid, "Delivery")
    with get_sessionmaker()() as s:
        card = Card(user_id=uid, bank="qik", last4="3333")
        s.add(card)
        s.flush()
        s.add(Transaction(user_id=uid, card_id=card.id, tx_type="consumo",
                          merchant="UBER EATS", txn_date=date.today(),
                          original_amount=500, currency="RD$", amount_dop=500,
                          category_id=delivery.id, dedupe_key="k-cat4"))
        s.add(Rule(user_id=uid, substring="UBER", category_id=delivery.id))
        s.add(Budget(user_id=uid, category_id=delivery.id,
                     month=date.today().replace(day=1), amount_dop=3000))
        s.commit()

    client.post(f"/categories/{delivery.id}/delete")

    with get_sessionmaker()() as s:
        assert s.get(Category, delivery.id) is None
        txn = s.scalar(select(Transaction).where(Transaction.user_id == uid))
        assert txn.category.name == "Otros / sin categoría"  # reassigned
        assert s.scalar(select(Rule).where(Rule.user_id == uid)) is None
        assert s.scalar(select(Budget).where(Budget.user_id == uid)) is None


def test_cannot_delete_system_category(client):
    uid = _signup_login(client, "cat5@example.com")
    otros = _cat(uid, "Otros / sin categoría")
    client.post(f"/categories/{otros.id}/delete")
    assert _cat(uid, "Otros / sin categoría") is not None


def test_cannot_delete_another_users_category(client):
    b_uid = _signup_login(client, "catb@example.com")
    client.post("/categories", data={"name": "Privada B"})
    bid = _cat(b_uid, "Privada B").id

    _signup_login(client, "cata@example.com")  # switch session to A
    client.post(f"/categories/{bid}/delete")

    from web.app.db import get_sessionmaker
    from web.app.models import Category
    with get_sessionmaker()() as s:
        assert s.get(Category, bid) is not None
