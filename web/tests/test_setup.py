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


def test_status_surfaces_own_confirmation_code(client):
    uid = _signup_login(client, "s6@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        s.add(RawEmail(
            user_id=uid, provider_message_id="c1", note="123456789",
            processing_status="confirmation",
            subject="(Gmail Forwarding Confirmation - Receive Mail from s6@example.com)"))
        s.commit()
    body = client.get("/setup/status").json()
    assert body["confirmations"] == [
        {"kind": "code", "value": "123456789", "source": "s6@example.com"}]
    assert body["tx_count"] == 0


def test_status_surfaces_own_confirmation_link(client):
    uid = _signup_login(client, "s7@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    link = "https://mail.google.com/mail/vf-%5Babc%5D-xyz"
    with get_sessionmaker()() as s:
        # Case-insensitive match against the registered email.
        s.add(RawEmail(
            user_id=uid, provider_message_id="c2", note=link,
            processing_status="confirmation",
            subject="(Gmail Forwarding Confirmation - Receive Mail from S7@Example.com)"))
        s.commit()
    body = client.get("/setup/status").json()
    assert body["confirmations"] == [
        {"kind": "link", "value": link, "source": "S7@Example.com"}]


def test_status_hides_other_peoples_confirmations(client):
    """Only the confirmation from the user's own registered Gmail is shown; a
    forward someone else set up to this inbound address is never surfaced."""
    uid = _signup_login(client, "s8@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail

    def conf(mid, source, note):
        return RawEmail(
            user_id=uid, provider_message_id=mid, note=note,
            processing_status="confirmation",
            subject=f"(Gmail Forwarding Confirmation - Receive Mail from {source})")

    with get_sessionmaker()() as s:
        s.add(conf("own", "s8@example.com", "https://mail.google.com/mail/vf-OWN"))
        s.add(conf("other", "stranger@gmail.com", "https://mail.google.com/mail/vf-OTHER"))
        s.commit()

    confs = client.get("/setup/status").json()["confirmations"]
    assert [c["source"] for c in confs] == ["s8@example.com"]
    assert confs[0]["value"].endswith("vf-OWN")


def test_setup_page_shows_only_own_confirmation(client):
    """The rendered /setup page labels the user's own confirmation and omits a
    stranger's forward to the same inbound address."""
    uid = _signup_login(client, "s9@example.com")
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        for mid, src in [("own", "s9@example.com"), ("other", "bob@gmail.com")]:
            s.add(RawEmail(
                user_id=uid, provider_message_id=mid,
                note=f"https://mail.google.com/mail/vf-{mid}",
                processing_status="confirmation",
                subject=f"(Gmail Forwarding Confirmation - Receive Mail from {src})"))
        s.commit()
    html = client.get("/setup").text
    assert 'data-source="s9@example.com"' in html
    assert "bob@gmail.com" not in html
    assert html.count("Confirmar reenvío en Gmail") == 1
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


def test_backfill_query_is_date_bounded(client):
    """Step 3's search must carry its own date bound. Unbounded, users select by
    eye and import months of old mail the current-month dashboard never shows."""
    from datetime import date, timedelta
    _signup_login(client, "r4@example.com")
    bound = date.today().replace(day=1) - timedelta(days=1)
    html = client.get("/setup").text
    assert f"after:{bound:%Y/%m/%d}" in html


def _add_confirmation(uid, source, note="https://mail.google.com/mail/vf-OK",
                      mid="conf"):
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        s.add(RawEmail(
            user_id=uid, provider_message_id=mid, note=note,
            processing_status="confirmation",
            subject=f"(Gmail Forwarding Confirmation - Receive Mail from {source})"))
        s.commit()


def test_filter_step_locked_until_forwarding_is_confirmed(client):
    """Gmail rejects a filter import whose forwardTo isn't a confirmed
    forwarding address, and says nothing useful about why — so step 2 states the
    prerequisite and stays inert until step 1 produces a confirmation."""
    _signup_login(client, "r6@example.com")
    html = client.get("/setup").text
    assert 'class="card step locked" id="step-filter-card"' in html
    assert 'class="status-line" id="filter-locked"' in html  # the waiting note shows
    assert 'aria-disabled="true"' in html                    # download is inert
    assert "Primero termina el paso 1." in html


def test_filter_step_unlocks_once_a_confirmation_arrives(client):
    uid = _signup_login(client, "r7@example.com")
    _add_confirmation(uid, "r7@example.com")
    html = client.get("/setup").text
    assert 'class="card step" id="step-filter-card"' in html
    assert 'class="status-line hidden" id="filter-locked"' in html
    assert 'aria-disabled="true"' not in html
    assert "Primero termina el paso 1." in html  # the ordering note stays


def test_filter_step_unlocked_when_transactions_already_flow(client):
    """A confirmation row aged out by the 30-day purge must not re-lock a step
    for someone whose mail is demonstrably already arriving."""
    from datetime import date
    from web.app.db import get_sessionmaker
    from web.app.models import Card, Category, Transaction
    uid = _signup_login(client, "r8@example.com")
    with get_sessionmaker()() as s:
        card = Card(user_id=uid, bank="qik", last4="4444")
        s.add(card)
        s.flush()
        cat = s.scalar(select(Category).where(Category.user_id == uid,
                                              Category.name == "Delivery"))
        s.add(Transaction(user_id=uid, card_id=card.id, tx_type="consumo",
                          merchant="X", txn_date=date.today(), original_amount=10,
                          currency="RD$", amount_dop=10, category_id=cat.id,
                          dedupe_key="k-r8"))
        s.commit()
    html = client.get("/setup").text
    assert 'class="card step" id="step-filter-card"' in html
    assert 'aria-disabled="true"' not in html


def test_gmail_filter_is_not_date_bounded(client):
    """The *filter* must stay unbounded — a date bound there would stop
    forwarding as soon as the month rolls over."""
    _signup_login(client, "r5@example.com")
    xml = client.get("/setup/gmail-filter.xml").text
    assert "name='from'" in xml
    assert "notificaciones@popularenlinea.com" in xml
    assert "after:" not in xml


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
