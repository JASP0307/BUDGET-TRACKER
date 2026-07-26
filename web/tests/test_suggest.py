"""LLM category suggestions: the Ollama client, the background sweep, and the
dashboard accept/dismiss flow (accept also mints a rule + retro-applies)."""

import json
from datetime import date

import requests
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
    """One consumo in «Otros / sin categoría», with a card to hang it on."""
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


class _FakeResponse:
    def __init__(self, category):
        self._category = category

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": json.dumps({"category": self._category})}}


# ---- suggest_category (Ollama client) ----

def test_suggest_category_returns_valid_name(monkeypatch):
    from web.app.services import suggest
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["url"] = url
        seen["body"] = json
        return _FakeResponse("Delivery")

    monkeypatch.setattr(suggest.requests, "post", fake_post)
    got = suggest.suggest_category("UBER EATS", ["Delivery", "Renta"],
                                   url="http://x", model="m")
    assert got == "Delivery"
    assert seen["url"] == "http://x/api/chat"
    # The schema enum pins the answer to the user's own categories (or null).
    assert seen["body"]["format"]["properties"]["category"]["enum"] == [
        "Delivery", "Renta", None]


def test_suggest_category_null_and_garbage_and_errors(monkeypatch):
    from web.app.services import suggest

    monkeypatch.setattr(suggest.requests, "post",
                        lambda *a, **k: _FakeResponse(None))
    assert suggest.suggest_category("X", ["A"], url="u", model="m") is None

    # A name outside the user's list is rejected even if the model emits it.
    monkeypatch.setattr(suggest.requests, "post",
                        lambda *a, **k: _FakeResponse("Nonexistent"))
    assert suggest.suggest_category("X", ["A"], url="u", model="m") is None

    def boom(*a, **k):
        raise requests.ConnectionError("ollama down")
    monkeypatch.setattr(suggest.requests, "post", boom)
    assert suggest.suggest_category("X", ["A"], url="u", model="m") is None


# ---- sweep ----

def test_sweep_creates_suggestions_and_dedupes_merchants(client, monkeypatch):
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestion
    from web.app.services import suggest

    uid = _signup_login(client, "sug1@example.com")
    _add_uncategorized_txn(uid, "UBER EATS RES", "k-s1")
    _add_uncategorized_txn(uid, "UBER EATS RES", "k-s2")  # same merchant
    _add_uncategorized_txn(uid, "FERRETERIA XYZ", "k-s3")

    calls = []

    def fake(merchant, categories, *, url, model):
        calls.append(merchant)
        return "Delivery" if "UBER" in merchant else None

    monkeypatch.setattr(suggest, "suggest_category", fake)
    with get_sessionmaker()() as s:
        created = suggest.sweep(s)
        s.commit()

    assert created == 2                 # both UBER twins, not the ferreteria
    assert len(calls) == 2              # one model call per distinct merchant
    with get_sessionmaker()() as s:
        rows = s.scalars(select(CategorySuggestion)
                         .where(CategorySuggestion.user_id == uid)).all()
        assert len(rows) == 2
        assert all(r.category.name == "Delivery" for r in rows)
        # A second sweep asks nothing new: suggested txns are excluded, and the
        # unmatched merchant is retried (it has no row marking it as asked).
        calls.clear()
        assert suggest.sweep(s) == 0
        assert calls == ["FERRETERIA XYZ"]


def test_sweep_disabled_without_ollama_url(client, monkeypatch):
    from web.app.db import get_sessionmaker
    from web.app.services import suggest

    uid = _signup_login(client, "sug2@example.com")
    _add_uncategorized_txn(uid, "COLMADO DONA ANA", "k-s4")
    monkeypatch.setenv("BUDGET_OLLAMA_URL", "")
    monkeypatch.setattr(suggest, "suggest_category",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    with get_sessionmaker()() as s:
        assert suggest.sweep(s) == 0


# ---- dashboard accept / dismiss ----

def _suggest(uid, txn_id, category_id):
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestion
    with get_sessionmaker()() as s:
        s.add(CategorySuggestion(user_id=uid, transaction_id=txn_id,
                                 category_id=category_id, model="ollama:test"))
        s.commit()


def test_accept_applies_category_mints_rule_and_retroapplies(client):
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestion, Rule, Transaction

    uid = _signup_login(client, "sug3@example.com")
    delivery = _cat(uid, "Delivery")
    txn_id = _add_uncategorized_txn(uid, "UBER EATS RES", "k-s5")
    sibling_id = _add_uncategorized_txn(uid, "UBER EATS RES2", "k-s6")
    _suggest(uid, txn_id, delivery.id)
    _suggest(uid, sibling_id, delivery.id)

    client.post(f"/transactions/{txn_id}/accept-suggestion")

    with get_sessionmaker()() as s:
        assert s.get(Transaction, txn_id).category_id == delivery.id
        # The minted rule ("UBER EATS RES") retro-applied to the sibling too.
        rule = s.scalar(select(Rule).where(Rule.user_id == uid))
        assert rule is not None and rule.substring == "UBER EATS RES"
        assert rule.category_id == delivery.id
        assert s.get(Transaction, sibling_id).category_id == delivery.id
        # Both suggestions are gone: one accepted, one pruned as stale.
        assert s.scalars(select(CategorySuggestion)
                         .where(CategorySuggestion.user_id == uid)).all() == []


def test_dismiss_keeps_row_and_hides_chip(client):
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestion

    uid = _signup_login(client, "sug4@example.com")
    delivery = _cat(uid, "Delivery")
    txn_id = _add_uncategorized_txn(uid, "PIZZARELLI", "k-s7")
    _suggest(uid, txn_id, delivery.id)

    assert "Sugerido" in client.get("/").text
    client.post(f"/transactions/{txn_id}/dismiss-suggestion")

    assert "Sugerido" not in client.get("/").text
    with get_sessionmaker()() as s:  # row kept so the sweep won't re-ask
        row = s.scalar(select(CategorySuggestion)
                       .where(CategorySuggestion.transaction_id == txn_id))
        assert row is not None and row.dismissed is True


def test_manual_recategorize_clears_suggestion(client):
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestion

    uid = _signup_login(client, "sug5@example.com")
    delivery = _cat(uid, "Delivery")
    renta = _cat(uid, "Renta")
    txn_id = _add_uncategorized_txn(uid, "PIZZARELLI", "k-s8")
    _suggest(uid, txn_id, delivery.id)

    client.post(f"/transactions/{txn_id}/category",
                data={"category_id": str(renta.id)})

    with get_sessionmaker()() as s:
        assert s.scalar(select(CategorySuggestion)
                        .where(CategorySuggestion.transaction_id == txn_id)) is None


def test_cross_user_cannot_accept_or_dismiss(client):
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestion, Rule, Transaction

    b_uid = _signup_login(client, "sugb@example.com")
    delivery = _cat(b_uid, "Delivery")
    txn_id = _add_uncategorized_txn(b_uid, "UBER EATS RES", "k-s9")
    _suggest(b_uid, txn_id, delivery.id)

    _signup_login(client, "suga@example.com")  # switch session to A
    client.post(f"/transactions/{txn_id}/accept-suggestion")
    client.post(f"/transactions/{txn_id}/dismiss-suggestion")

    with get_sessionmaker()() as s:
        txn = s.get(Transaction, txn_id)
        assert txn.category.name == "Otros / sin categoría"  # untouched
        assert s.scalar(select(Rule).where(Rule.user_id == b_uid)) is None
        row = s.scalar(select(CategorySuggestion)
                       .where(CategorySuggestion.transaction_id == txn_id))
        assert row is not None and row.dismissed is False
