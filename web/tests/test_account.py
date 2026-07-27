"""Account page: data export and account deletion.

These are the user's own controls, so the tests care about two things — the
export contains this user's data and nobody else's, and deletion actually
removes every row rather than leaving orphans behind.
"""

from pathlib import Path

from sqlalchemy import func, select

from web.app import ratelimit

FIXTURES = Path(__file__).parent.parent.parent / "core" / "tests" / "fixtures"


def _user(email):
    from web.app.db import get_sessionmaker
    from web.app.models import User
    with get_sessionmaker()() as s:
        return s.scalar(select(User).where(func.lower(User.email) == email.lower()))


def _signup(client, email, pw="supersecret"):
    from web.app.auth.security import make_verify_token
    client.post("/register", data={"email": email, "password": pw},
                follow_redirects=True)
    uid = _user(email).id
    client.get(f"/verify?token={make_verify_token(uid)}", follow_redirects=True)
    ratelimit.limiter.reset()
    return uid


def _login(client, email, pw="supersecret"):
    client.post("/login", data={"email": email, "password": pw})


def _ingest_a_transaction(client, user_id, message_id="acct-1"):
    """Push one real bank email through the webhook for this user."""
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    with get_sessionmaker()() as s:
        token = s.scalar(select(InboundAddress).where(
            InboundAddress.user_id == user_id)).token
    client.post("/webhooks/postmark-inbound/testsecret", json={
        "MessageID": message_id,
        "From": "notificaciones@qik.do",
        "Subject": "Usaste tu tarjeta de crédito Qik",
        "ToFull": [{"Email": f"u_{token}@in.example.do"}],
        "HtmlBody": (FIXTURES / "qik_purchase.html").read_text(encoding="utf-8"),
        "Headers": [],
    })


def test_account_page_requires_login(client):
    r = client.get("/account", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_account_page_loads(client):
    _signup(client, "acct@example.com")
    _login(client, "acct@example.com")
    r = client.get("/account")
    assert r.status_code == 200 and "Eliminar mi cuenta" in r.text


# ---- export ----

def test_export_contains_the_users_data(client):
    uid = _signup(client, "exp@example.com")
    _ingest_a_transaction(client, uid)
    _login(client, "exp@example.com")

    r = client.get("/account/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]

    data = r.json()
    assert data["account"]["email"] == "exp@example.com"
    assert len(data["transactions"]) == 1
    assert data["transactions"][0]["merchant"] == "AMAZON 1"
    assert data["cards"][0]["last4"] == "3333"
    assert any(c["name"] == "Renta" for c in data["categories"])


def test_export_never_includes_another_users_data(client):
    a_id = _signup(client, "a-exp@example.com")
    _ingest_a_transaction(client, a_id, message_id="acct-a")
    b_id = _signup(client, "b-exp@example.com")
    _ingest_a_transaction(client, b_id, message_id="acct-b")

    _login(client, "b-exp@example.com")
    data = client.get("/account/export").json()
    assert data["account"]["email"] == "b-exp@example.com"
    assert len(data["transactions"]) == 1  # only B's


def test_export_carries_no_full_card_number(client):
    """Belt and braces on the claim made on the privacy page."""
    uid = _signup(client, "card@example.com")
    _ingest_a_transaction(client, uid)
    _login(client, "card@example.com")
    for card in client.get("/account/export").json()["cards"]:
        assert len(card["last4"]) == 4


# ---- deletion ----

def test_delete_requires_the_confirmation_word(client):
    _signup(client, "del1@example.com")
    _login(client, "del1@example.com")
    r = client.post("/account/delete", data={"password": "supersecret",
                                             "confirm": "si"})
    assert "ELIMINAR" in r.text
    assert _user("del1@example.com") is not None


def test_delete_requires_the_password(client):
    _signup(client, "del2@example.com")
    _login(client, "del2@example.com")
    r = client.post("/account/delete", data={"password": "wrongpass",
                                             "confirm": "eliminar"})
    assert "Contraseña incorrecta" in r.text
    assert _user("del2@example.com") is not None


def test_delete_removes_every_row_for_the_user(client):
    from web.app.db import get_sessionmaker
    from web.app.models import (Budget, Card, Category, InboundAddress,
                                NotificationPref, RawEmail, Rule, Transaction,
                                User)

    uid = _signup(client, "del3@example.com")
    _ingest_a_transaction(client, uid)
    _login(client, "del3@example.com")

    r = client.post("/account/delete", data={"password": "supersecret",
                                             "confirm": "ELIMINAR"})
    assert "eliminada" in r.text.lower()

    with get_sessionmaker()() as s:
        assert s.get(User, uid) is None
        for model in (Transaction, Rule, Budget, NotificationPref, RawEmail,
                      Card, Category, InboundAddress):
            left = s.scalars(select(model).where(model.user_id == uid)).all()
            assert left == [], f"{model.__name__} rows survived deletion"


def test_delete_leaves_other_accounts_intact(client):
    from web.app.db import get_sessionmaker
    from web.app.models import Transaction

    keep_id = _signup(client, "keep@example.com")
    _ingest_a_transaction(client, keep_id, message_id="acct-keep")
    gone_id = _signup(client, "gone@example.com")
    _ingest_a_transaction(client, gone_id, message_id="acct-gone")

    _login(client, "gone@example.com")
    client.post("/account/delete", data={"password": "supersecret",
                                         "confirm": "eliminar"})

    with get_sessionmaker()() as s:
        assert _user("keep@example.com") is not None
        assert s.scalars(select(Transaction).where(
            Transaction.user_id == keep_id)).all() != []


def test_delete_logs_the_user_out(client):
    _signup(client, "del4@example.com")
    _login(client, "del4@example.com")
    client.post("/account/delete", data={"password": "supersecret",
                                         "confirm": "eliminar"})
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


# ---- Verified forwarders ----

def _forwarders(user_id):
    from web.app.db import get_sessionmaker
    from web.app.models import VerifiedForwarder
    with get_sessionmaker()() as s:
        return sorted(f.address for f in s.scalars(select(VerifiedForwarder)
                      .where(VerifiedForwarder.user_id == user_id)).all())


def test_adding_a_forwarder_stores_it_normalized(client):
    uid = _signup(client, "fwd1@example.com")
    _login(client, "fwd1@example.com")
    client.post("/account/forwarders/add", data={"address": "  Work@Example.COM "})
    assert _forwarders(uid) == ["work@example.com"]


def test_adding_the_same_forwarder_twice_is_harmless(client):
    uid = _signup(client, "fwd2@example.com")
    _login(client, "fwd2@example.com")
    for _ in range(2):
        client.post("/account/forwarders/add", data={"address": "work@example.com"})
    assert _forwarders(uid) == ["work@example.com"]


def test_removing_a_forwarder(client):
    uid = _signup(client, "fwd3@example.com")
    _login(client, "fwd3@example.com")
    client.post("/account/forwarders/add", data={"address": "work@example.com"})
    client.post("/account/forwarders/remove", data={"address": "work@example.com"})
    assert _forwarders(uid) == []


def test_cannot_remove_another_users_forwarder(client):
    """The remove handler scopes by user_id, so B cannot strip A's trust."""
    a_id = _signup(client, "fwd-a@example.com")
    _login(client, "fwd-a@example.com")
    client.post("/account/forwarders/add", data={"address": "shared@example.com"})

    _signup(client, "fwd-b@example.com")
    _login(client, "fwd-b@example.com")
    client.post("/account/forwarders/remove", data={"address": "shared@example.com"})
    assert _forwarders(a_id) == ["shared@example.com"]


def test_forwarders_require_login(client):
    r = client.post("/account/forwarders/add", data={"address": "x@example.com"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_deleting_an_account_removes_its_forwarders(client):
    uid = _signup(client, "fwd-del@example.com")
    _login(client, "fwd-del@example.com")
    client.post("/account/forwarders/add", data={"address": "work@example.com"})
    client.post("/account/delete", data={"password": "supersecret",
                                         "confirm": "eliminar"})
    assert _forwarders(uid) == []
