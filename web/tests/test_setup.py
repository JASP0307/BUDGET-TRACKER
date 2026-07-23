"""Onboarding: card management, inbound address, and the live status endpoint."""

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


def test_setup_requires_login(client):
    r = client.get("/setup", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_setup_shows_inbound_address(client):
    _signup_login(client, "s1@example.com")
    r = client.get("/setup")
    assert r.status_code == 200
    assert "@in.example.do" in r.text


def test_add_card(client):
    _signup_login(client, "s2@example.com")
    client.post("/cards", data={"bank": "popular", "last4": "1111",
                                "label": "Visa ISI"})
    from web.app.db import get_sessionmaker
    from web.app.models import Card
    with get_sessionmaker()() as s:
        card = s.scalar(select(Card).where(Card.last4 == "1111"))
        assert card is not None and card.label == "Visa ISI" and not card.needs_review


def test_add_card_confirms_existing_needs_review(client):
    """A card auto-created during ingest (needs_review) is confirmed, not
    duplicated, when the user registers the same bank+last4."""
    uid = _signup_login(client, "s3@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import Card
    with get_sessionmaker()() as s:
        s.add(Card(user_id=uid, bank="qik", last4="3333", needs_review=True))
        s.commit()
    client.post("/cards", data={"bank": "qik", "last4": "3333", "label": "Qik"})
    with get_sessionmaker()() as s:
        cards = s.scalars(select(Card).where(Card.user_id == uid)).all()
        assert len(cards) == 1
        assert cards[0].label == "Qik" and not cards[0].needs_review


def test_invalid_last4_rejected(client):
    _signup_login(client, "s4@example.com")
    client.post("/cards", data={"bank": "popular", "last4": "12", "label": ""})
    from web.app.db import get_sessionmaker
    from web.app.models import Card
    with get_sessionmaker()() as s:
        assert s.scalar(select(Card)) is None


def test_delete_card_without_txns(client):
    uid = _signup_login(client, "s5@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import Card
    with get_sessionmaker()() as s:
        c = Card(user_id=uid, bank="popular", last4="0001")
        s.add(c)
        s.commit()
        cid = c.id
    client.post(f"/cards/{cid}/delete")
    with get_sessionmaker()() as s:
        assert s.get(Card, cid) is None


def test_status_surfaces_confirmation_code(client):
    uid = _signup_login(client, "s6@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        s.add(RawEmail(user_id=uid, provider_message_id="c1",
                       processing_status="confirmation", note="123456789"))
        s.commit()
    body = client.get("/setup/status").json()
    assert body["confirmation_code"] == "123456789"
    assert body["tx_count"] == 0


def test_cannot_touch_other_users_card(client):
    b_uid = _signup_login(client, "b@example.com")
    client.post("/cards", data={"bank": "popular", "last4": "7777", "label": "B card"})
    from web.app.db import get_sessionmaker
    from web.app.models import Card
    with get_sessionmaker()() as s:
        bid = s.scalar(select(Card).where(Card.user_id == b_uid,
                                          Card.last4 == "7777")).id

    _signup_login(client, "a2@example.com")  # switch session to A
    client.post(f"/cards/{bid}/label", data={"label": "hacked"})
    client.post(f"/cards/{bid}/delete")

    with get_sessionmaker()() as s:
        card = s.get(Card, bid)
        assert card is not None and card.label == "B card"
