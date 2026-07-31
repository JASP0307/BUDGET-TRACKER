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


class _RawResponse:
    """A backend that ignores `format` and answers with whatever it likes."""

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self._content}}


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


def test_suggest_category_survives_a_backend_that_ignores_the_schema(monkeypatch):
    """A backend that ignores `format` must never abort the sweep. These once
    escaped as an AttributeError from .get() and killed the whole run; they
    must read as "no answer" instead."""
    from web.app.services import suggest

    for content in ("null",   # valid JSON, not an object
                    "[]",     # valid JSON, not an object
                    "",       # empty body
                    "Category: **Food Delivery Services**",  # names nothing real
                    "I would put this under Delivery or maybe Renta"):  # ambiguous
        monkeypatch.setattr(suggest.requests, "post",
                            lambda *a, _c=content, **k: _RawResponse(_c))
        assert suggest.suggest_category(
            "X", ["Delivery", "Renta"], url="u", model="m") is None


def test_suggest_category_accepts_an_unwrapped_answer(monkeypatch):
    """Every Ollama *cloud* model measured (gemma4:cloud, gpt-oss:20b-cloud,
    gpt-oss:120b-cloud) ignores the `format` schema and replies with the bare
    category name — the correct one, just not wrapped in the JSON object. The
    strict json.loads path used to discard those as "no answer", which is what
    made cloud models unusable here."""
    from web.app.services import suggest

    cats = ["Suscripciones", "Combustible"]
    for content, expected in (
            ("Suscripciones", "Suscripciones"),            # the real cloud reply
            ("suscripciones", "Suscripciones"),            # model's own casing
            ("**Combustible**", "Combustible"),            # markdown decoration
            ('"Suscripciones"', "Suscripciones"),          # a quoted JSON string
            ('```json\n{"category": "Combustible"}\n```', "Combustible"),  # fenced
            ('Sure! {"category": "Combustible"}', "Combustible"),  # JSON in prose
            ("The category is Combustible.", "Combustible"),       # plain prose
            ("Ninguna", None),                             # longhand abstain
    ):
        monkeypatch.setattr(suggest.requests, "post",
                            lambda *a, _c=content, **k: _RawResponse(_c))
        assert suggest.suggest_category(
            "X", cats, url="u", model="m") == expected, content


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
        result = suggest.sweep(s)
        s.commit()

    assert result.created == 2          # both UBER twins, not the ferreteria
    assert result.calls == 2            # one model call per distinct merchant
    assert len(calls) == 2              # one model call per distinct merchant
    with get_sessionmaker()() as s:
        rows = s.scalars(select(CategorySuggestion)
                         .where(CategorySuggestion.user_id == uid)).all()
        assert len(rows) == 2
        assert all(r.category.name == "Delivery" for r in rows)
        # A second sweep asks nothing new: suggested txns are excluded, and the
        # unmatched merchant was recorded as a miss on the first sweep, so it
        # isn't retried either.
        calls.clear()
        result2 = suggest.sweep(s)
        assert result2.created == 0
        assert result2.calls == 0
        assert calls == []


def test_sweep_does_not_reask_a_missed_merchant(client, monkeypatch):
    """An abstained merchant gets a CategorySuggestionMiss row on first sight
    and must never reach Ollama again — not even via a brand new transaction
    for the same merchant text in a later run."""
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestionMiss
    from web.app.services import suggest

    uid = _signup_login(client, "sug8@example.com")
    _add_uncategorized_txn(uid, "INVERSIONES OPACA", "k-s12")

    monkeypatch.setattr(suggest, "suggest_category", lambda *a, **k: None)
    with get_sessionmaker()() as s:
        result = suggest.sweep(s)
        s.commit()
    assert result.calls == 1

    with get_sessionmaker()() as s:
        misses = s.scalars(select(CategorySuggestionMiss)
                           .where(CategorySuggestionMiss.user_id == uid)).all()
        assert len(misses) == 1
        assert misses[0].merchant == "INVERSIONES OPACA"

    # A second, later transaction for the exact same merchant must not be
    # re-sent to the model either.
    _add_uncategorized_txn(uid, "inversiones opaca", "k-s13")  # different case
    monkeypatch.setattr(suggest, "suggest_category",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    with get_sessionmaker()() as s:
        result2 = suggest.sweep(s)
        assert result2.calls == 0
        assert result2.created == 0


def _backdate_miss(user_id, merchant, days):
    from datetime import datetime, timedelta, timezone

    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestionMiss
    with get_sessionmaker()() as s:
        row = s.scalar(select(CategorySuggestionMiss).where(
            CategorySuggestionMiss.user_id == user_id,
            CategorySuggestionMiss.merchant == merchant))
        row.checked_at = datetime.now(timezone.utc) - timedelta(days=days)
        s.commit()


def test_sweep_reasks_a_miss_past_the_ttl(client, monkeypatch):
    """A miss older than MISS_TTL_DAYS is fair game again — a merchant the
    model got wrong once shouldn't be blocked forever, e.g. once the user has
    since added a category that would now fit it."""
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestionMiss
    from web.app.services import suggest

    uid = _signup_login(client, "sug9@example.com")
    _add_uncategorized_txn(uid, "TALLER EL PRIMO", "k-s14")

    monkeypatch.setattr(suggest, "suggest_category", lambda *a, **k: None)
    with get_sessionmaker()() as s:
        suggest.sweep(s)
        s.commit()

    _backdate_miss(uid, "TALLER EL PRIMO", suggest.MISS_TTL_DAYS + 1)

    calls = []
    monkeypatch.setattr(suggest, "suggest_category",
                        lambda m, *a, **k: calls.append(m) or "Mantenimiento")
    with get_sessionmaker()() as s:
        result = suggest.sweep(s)
        s.commit()

    assert calls == ["TALLER EL PRIMO"]
    assert result.created == 1
    with get_sessionmaker()() as s:
        # Resolved — the stale miss row is cleared, not left as debris.
        assert s.scalar(select(CategorySuggestionMiss)
                        .where(CategorySuggestionMiss.user_id == uid)) is None


def test_sweep_reasks_after_a_model_change(client, monkeypatch):
    """A miss recorded under one BUDGET_OLLAMA_MODEL doesn't block the same
    merchant once a different model is configured — the old model may simply
    have been worse."""
    from web.app.db import get_sessionmaker
    from web.app.models import CategorySuggestionMiss
    from web.app.services import suggest

    uid = _signup_login(client, "sug10@example.com")
    _add_uncategorized_txn(uid, "FERRETERIA DON PEPE", "k-s15")

    monkeypatch.setattr(suggest, "suggest_category", lambda *a, **k: None)
    with get_sessionmaker()() as s:
        suggest.sweep(s)
        s.commit()
    with get_sessionmaker()() as s:
        miss = s.scalar(select(CategorySuggestionMiss)
                        .where(CategorySuggestionMiss.user_id == uid))
        assert miss.model == "ollama:qwen2.5:3b"

    monkeypatch.setenv("BUDGET_OLLAMA_MODEL", "qwen2.5:7b")
    calls = []
    monkeypatch.setattr(suggest, "suggest_category",
                        lambda m, *a, **k: calls.append(m) or None)
    with get_sessionmaker()() as s:
        result = suggest.sweep(s)
        s.commit()

    assert calls == ["FERRETERIA DON PEPE"]  # asked again under the new model
    assert result.calls == 1
    with get_sessionmaker()() as s:
        # Same row, refreshed in place — not a second row for the same merchant.
        misses = s.scalars(select(CategorySuggestionMiss)
                           .where(CategorySuggestionMiss.user_id == uid)).all()
        assert len(misses) == 1
        assert misses[0].model == "ollama:qwen2.5:7b"


def test_sweep_reports_calls_even_when_every_answer_is_null(client, monkeypatch):
    """A run where the model answers null for every merchant still hit
    Ollama — `calls` must reflect that even though `created` stays 0."""
    from web.app.db import get_sessionmaker
    from web.app.services import suggest

    uid = _signup_login(client, "sug7@example.com")
    _add_uncategorized_txn(uid, "INVERSIONES OPACA", "k-s11")

    monkeypatch.setattr(suggest, "suggest_category", lambda *a, **k: None)
    with get_sessionmaker()() as s:
        result = suggest.sweep(s)

    assert result.created == 0
    assert result.calls == 1


def test_sweep_never_offers_system_categories(client, monkeypatch):
    """«Otros / sin categoría» is the state we're leaving and "Retiro Efectivo"
    comes from the transaction type, so neither may be a candidate answer."""
    from web.app.db import get_sessionmaker
    from web.app.services import suggest

    uid = _signup_login(client, "sug6@example.com")
    _add_uncategorized_txn(uid, "INVERSIONES TAKATA", "k-s10")

    offered = []

    def fake(merchant, categories, *, url, model):
        offered.extend(categories)
        return None

    monkeypatch.setattr(suggest, "suggest_category", fake)
    with get_sessionmaker()() as s:
        suggest.sweep(s)

    assert offered, "the model was never asked"
    assert "Retiro Efectivo" not in offered
    assert "Otros / sin categoría" not in offered
    assert "Delivery" in offered  # ordinary categories still offered


def test_sweep_disabled_without_ollama_url(client, monkeypatch):
    from web.app.db import get_sessionmaker
    from web.app.services import suggest

    uid = _signup_login(client, "sug2@example.com")
    _add_uncategorized_txn(uid, "COLMADO DONA ANA", "k-s4")
    monkeypatch.setenv("BUDGET_OLLAMA_URL", "")
    monkeypatch.setattr(suggest, "suggest_category",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    with get_sessionmaker()() as s:
        result = suggest.sweep(s)
        assert result.created == 0
        assert result.calls == 0


# ---- notify_sweep ----

def _owner_id():
    from sqlalchemy import func, select
    from web.app.db import get_sessionmaker
    from web.app.models import User
    from web.app.settings import get_settings
    with get_sessionmaker()() as s:
        return s.scalar(select(User).where(
            func.lower(User.email) == get_settings().default_user_email.lower())).id


def _link_owner_telegram(chat_id="999"):
    from web.app.db import get_sessionmaker
    from web.app.services import notify
    with get_sessionmaker()() as s:
        notify.set_telegram_chat(s, _owner_id(), chat_id)


def test_notify_sweep_sends_when_owner_linked_and_calls_made(monkeypatch, client):
    from web.app.db import get_sessionmaker
    from web.app.services import notify, telegram

    _link_owner_telegram()
    sent = []
    monkeypatch.setattr(telegram, "send_message",
                        lambda chat_id, text: sent.append((chat_id, text)))

    with get_sessionmaker()() as s:
        notify.notify_sweep(s, calls=3, created=1, elapsed=12.5)

    assert len(sent) == 1
    assert sent[0][0] == "999"
    assert "3" in sent[0][1] and "1" in sent[0][1]


def test_notify_sweep_noop_without_linked_telegram(monkeypatch, client):
    from web.app.db import get_sessionmaker
    from web.app.services import notify, telegram

    sent = []
    monkeypatch.setattr(telegram, "send_message",
                        lambda chat_id, text: sent.append((chat_id, text)))

    with get_sessionmaker()() as s:
        notify.notify_sweep(s, calls=3, created=1, elapsed=12.5)

    assert sent == []


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
