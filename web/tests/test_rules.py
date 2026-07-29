"""Rules minted straight from a dashboard transaction row ("set as rule")."""

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


def _add_uncategorized_txn(uid, merchant, dedupe_key):
    from budgetcore.categorize import UNCATEGORIZED
    from web.app.db import get_sessionmaker
    from web.app.models import Card, Category, Transaction
    with get_sessionmaker()() as s:
        card = s.scalar(select(Card).where(Card.user_id == uid))
        if card is None:
            card = Card(user_id=uid, bank="qik", last4="3333")
            s.add(card)
            s.flush()
        otros = s.scalar(select(Category).where(
            Category.user_id == uid, Category.name == UNCATEGORIZED))
        txn = Transaction(user_id=uid, card_id=card.id, tx_type="consumo",
                          merchant=merchant, txn_date=date.today(),
                          original_amount=500, currency="RD$", amount_dop=500,
                          category_id=otros.id, dedupe_key=dedupe_key)
        s.add(txn)
        s.commit()
        return txn.id


def _txn_category(txn_id):
    from web.app.db import get_sessionmaker
    from web.app.models import Transaction
    with get_sessionmaker()() as s:
        return s.get(Transaction, txn_id).category.name


def _rules(uid):
    from web.app.db import get_sessionmaker
    from web.app.models import Rule
    with get_sessionmaker()() as s:
        return [(r.substring, r.category.name) for r in s.scalars(
            select(Rule).where(Rule.user_id == uid))]


def test_button_and_popover_render_on_each_row(client):
    uid = _signup_login(client, "rule1@example.com")
    txn_id = _add_uncategorized_txn(uid, "SUPERMERCADO NACIONAL", "r1")
    body = client.get("/").text
    assert f'popovertarget="rule-{txn_id}"' in body
    assert f'action="/transactions/{txn_id}/rule"' in body
    # merchant arrives prefilled, upper-cased, ready to be trimmed
    assert 'value="SUPERMERCADO NACIONAL"' in body


def test_creates_rule_and_moves_the_transaction(client):
    uid = _signup_login(client, "rule2@example.com")
    txn_id = _add_uncategorized_txn(uid, "SUPERMERCADO NACIONAL 042", "r1")
    delivery = _cat(uid, "Delivery")

    r = client.post(f"/transactions/{txn_id}/rule",
                    data={"substring": "supermercado nacional",
                          "category_id": str(delivery.id)},
                    follow_redirects=False)

    assert r.status_code == 303
    assert _rules(uid) == [("SUPERMERCADO NACIONAL", "Delivery")]
    assert _txn_category(txn_id) == "Delivery"


def test_retro_applies_to_sibling_uncategorized_transactions(client):
    uid = _signup_login(client, "rule3@example.com")
    first = _add_uncategorized_txn(uid, "UBER EATS 1", "r1")
    sibling = _add_uncategorized_txn(uid, "UBER EATS 2", "r2")
    untouched = _add_uncategorized_txn(uid, "FARMACIA CAROL", "r3")
    delivery = _cat(uid, "Delivery")

    client.post(f"/transactions/{first}/rule",
                data={"substring": "UBER EATS", "category_id": str(delivery.id)})

    assert _txn_category(sibling) == "Delivery"
    assert _txn_category(untouched) == "Otros / sin categoría"


def test_same_substring_retargets_instead_of_duplicating(client):
    uid = _signup_login(client, "rule4@example.com")
    txn_id = _add_uncategorized_txn(uid, "UBER EATS", "r1")
    delivery = _cat(uid, "Delivery")
    salidas = _cat(uid, "Salidas")

    client.post(f"/transactions/{txn_id}/rule",
                data={"substring": "UBER EATS", "category_id": str(salidas.id)})
    client.post(f"/transactions/{txn_id}/rule",
                data={"substring": "UBER EATS", "category_id": str(delivery.id)})

    assert _rules(uid) == [("UBER EATS", "Delivery")]
    assert _txn_category(txn_id) == "Delivery"


def test_moves_a_transaction_that_was_already_categorized_by_hand(client):
    """The retro-apply pass only touches uncategorized rows; the transaction the
    rule was made from has to move on its own."""
    uid = _signup_login(client, "rule5@example.com")
    txn_id = _add_uncategorized_txn(uid, "JUMBO", "r1")
    salidas = _cat(uid, "Salidas")
    delivery = _cat(uid, "Delivery")
    client.post(f"/transactions/{txn_id}/category",
                data={"category_id": str(salidas.id)})

    client.post(f"/transactions/{txn_id}/rule",
                data={"substring": "JUMBO", "category_id": str(delivery.id)})

    assert _txn_category(txn_id) == "Delivery"


def test_blank_substring_creates_nothing(client):
    uid = _signup_login(client, "rule6@example.com")
    txn_id = _add_uncategorized_txn(uid, "JUMBO", "r1")
    delivery = _cat(uid, "Delivery")

    client.post(f"/transactions/{txn_id}/rule",
                data={"substring": "   ", "category_id": str(delivery.id)})

    assert _rules(uid) == []
    assert _txn_category(txn_id) == "Otros / sin categoría"


def test_fallback_category_is_neither_offered_nor_accepted(client):
    """A rule → «Otros / sin categoría» is a no-op that would still shadow every
    later rule for the merchant, so it is off the menu and refused server-side."""
    uid = _signup_login(client, "rule9@example.com")
    txn_id = _add_uncategorized_txn(uid, "JUMBO", "r1")
    otros = _cat(uid, "Otros / sin categoría")

    body = client.get("/").text
    popcard = body.split(f'id="rule-{txn_id}"')[1].split("</div>")[0]
    assert str(otros.id) not in popcard
    assert "Elige una categoría" in popcard  # placeholder, nothing preselected

    client.post(f"/transactions/{txn_id}/rule",
                data={"substring": "JUMBO", "category_id": str(otros.id)})
    assert _rules(uid) == []


def test_cannot_make_a_rule_from_another_users_transaction(client):
    victim = _signup_login(client, "rule7a@example.com")
    victim_txn = _add_uncategorized_txn(victim, "JUMBO", "r1")
    client.post("/logout")

    attacker = _signup_login(client, "rule7b@example.com")
    delivery = _cat(attacker, "Delivery")
    client.post(f"/transactions/{victim_txn}/rule",
                data={"substring": "JUMBO", "category_id": str(delivery.id)})

    assert _rules(attacker) == []
    assert _txn_category(victim_txn) == "Otros / sin categoría"


def test_cannot_point_a_rule_at_another_users_category(client):
    victim = _signup_login(client, "rule8a@example.com")
    victim_delivery = _cat(victim, "Delivery")
    client.post("/logout")

    attacker = _signup_login(client, "rule8b@example.com")
    txn_id = _add_uncategorized_txn(attacker, "JUMBO", "r1")
    client.post(f"/transactions/{txn_id}/rule",
                data={"substring": "JUMBO", "category_id": str(victim_delivery.id)})

    assert _rules(attacker) == []
    assert _txn_category(txn_id) == "Otros / sin categoría"
