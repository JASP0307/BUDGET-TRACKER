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
    assert body["confirmations"] == [
        {"kind": "code", "value": "123456789", "source": None}]
    assert body["tx_count"] == 0


def test_status_surfaces_confirmation_link_with_source(client):
    uid = _signup_login(client, "s7@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    link = "https://mail.google.com/mail/vf-%5Babc%5D-xyz"
    with get_sessionmaker()() as s:
        s.add(RawEmail(
            user_id=uid, provider_message_id="c2", note=link,
            processing_status="confirmation",
            subject="(Gmail Forwarding Confirmation - Receive Mail from alice@gmail.com)"))
        s.commit()
    body = client.get("/setup/status").json()
    # The source account is pulled from Google's subject and labeled.
    assert body["confirmations"] == [
        {"kind": "link", "value": link, "source": "alice@gmail.com"}]


def test_status_lists_one_confirmation_per_source(client):
    """Forwarding from two accounts shows both, newest-per-source — not just the
    single most recent (which used to hide the others behind an unlabeled link)."""
    uid = _signup_login(client, "s8@example.com")
    from datetime import datetime, timezone
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail

    def conf(mid, source, note, minute):
        return RawEmail(
            user_id=uid, provider_message_id=mid, note=note,
            processing_status="confirmation",
            subject=f"(Gmail Forwarding Confirmation - Receive Mail from {source})",
            received_at=datetime(2026, 7, 25, 3, minute, tzinfo=timezone.utc))

    with get_sessionmaker()() as s:
        s.add(conf("a1", "alice@gmail.com", "https://mail.google.com/mail/vf-OLD", 10))
        s.add(conf("b1", "bob@gmail.com", "https://mail.google.com/mail/vf-BOB", 20))
        s.add(conf("a2", "alice@gmail.com", "https://mail.google.com/mail/vf-NEW", 30))
        s.commit()

    confs = client.get("/setup/status").json()["confirmations"]
    # Newest first, one per source; alice keeps her newest link.
    assert [c["source"] for c in confs] == ["alice@gmail.com", "bob@gmail.com"]
    assert confs[0]["value"].endswith("vf-NEW")


def test_setup_page_labels_each_confirmation_source(client):
    """The rendered /setup page shows one labeled confirm button per source."""
    uid = _signup_login(client, "s9@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        for mid, src in [("p1", "alice@gmail.com"), ("p2", "bob@gmail.com")]:
            s.add(RawEmail(
                user_id=uid, provider_message_id=mid,
                note=f"https://mail.google.com/mail/vf-{mid}",
                processing_status="confirmation",
                subject=f"(Gmail Forwarding Confirmation - Receive Mail from {src})"))
        s.commit()
    html = client.get("/setup").text
    assert 'data-source="alice@gmail.com"' in html
    assert 'data-source="bob@gmail.com"' in html
    assert html.count("Confirmar reenvío en Gmail") == 2
    assert 'id="confirm-waiting" style="display:none"' in html  # hidden once present


def test_home_redirects_new_user_to_setup(client):
    """A first-time user (no transactions yet) is routed into onboarding rather
    than shown an empty dashboard."""
    _signup_login(client, "r1@example.com")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"


def test_home_shows_dashboard_once_a_txn_exists(client):
    from datetime import date

    from web.app.db import get_sessionmaker
    from web.app.models import Card, Category, Transaction
    uid = _signup_login(client, "r2@example.com")
    with get_sessionmaker()() as s:
        card = Card(user_id=uid, bank="qik", last4="3333")
        s.add(card)
        s.flush()
        cat = s.scalar(select(Category).where(Category.user_id == uid,
                                              Category.name == "Delivery"))
        s.add(Transaction(user_id=uid, card_id=card.id, tx_type="consumo",
                          merchant="X", txn_date=date.today(), original_amount=10,
                          currency="RD$", amount_dop=10, category_id=cat.id,
                          dedupe_key="k-r2"))
        s.commit()
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_gmail_filter_xml_contains_inbound_address(client):
    from web.app.db import get_sessionmaker
    from web.app.models import InboundAddress
    uid = _signup_login(client, "r3@example.com")
    with get_sessionmaker()() as s:
        token = s.scalar(select(InboundAddress)
                         .where(InboundAddress.user_id == uid)).token
    r = client.get("/setup/gmail-filter.xml")
    assert r.status_code == 200
    assert "xml" in r.headers["content-type"]
    assert f"u_{token}@in.example.do" in r.text
    assert "forwardTo" in r.text


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
