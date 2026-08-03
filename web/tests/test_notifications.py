"""Notification prefs: Telegram account-linking via the webhook + the toggle,
disconnect, and page. No real Telegram calls happen (no bot token in tests)."""

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


def _login_owner(client):
    """The owner is the BUDGET_USER_EMAIL account created by bootstrap. Give it
    a password (bootstrap leaves it blank in tests) and sign in."""
    from web.app.auth.security import hash_password
    from web.app.db import get_sessionmaker
    from web.app.models import User
    from web.app.settings import get_settings
    email = get_settings().default_user_email
    with get_sessionmaker()() as s:
        owner = s.scalar(select(User).where(User.email == email))
        owner.hashed_password = hash_password("ownerpass1")
        s.commit()
    client.post("/login", data={"email": email, "password": "ownerpass1"})
    return email


def _link_chat(client, uid, chat_id=123):
    from web.app.services.telegram import make_link_token
    client.post("/webhooks/telegram/dev-tg-secret",
                json=_tg_update(f"/start {make_link_token(uid)}", chat_id=chat_id))


def _pref(uid):
    from web.app.db import get_sessionmaker
    from web.app.models import NotificationPref
    with get_sessionmaker()() as s:
        return s.scalar(select(NotificationPref).where(
            NotificationPref.user_id == uid,
            NotificationPref.channel == "telegram"))


def _tg_update(text, chat_id=555):
    return {"message": {"text": text, "chat": {"id": chat_id}}}


def test_notifications_page_loads(client):
    _signup_login(client, "n1@example.com")
    assert client.get("/notifications").status_code == 200


def test_start_links_chat(client):
    uid = _signup_login(client, "n2@example.com")
    from web.app.services.telegram import make_link_token
    token = make_link_token(uid)

    r = client.post("/webhooks/telegram/dev-tg-secret",
                    json=_tg_update(f"/start {token}", chat_id=999))
    assert r.status_code == 200 and r.json() == {"ok": True}

    pref = _pref(uid)
    assert pref is not None and pref.telegram_chat_id == "999" and pref.enabled


def test_start_with_bad_token_does_nothing(client):
    uid = _signup_login(client, "n3@example.com")
    client.post("/webhooks/telegram/dev-tg-secret",
                json=_tg_update("/start garbage"))
    assert _pref(uid) is None


def test_telegram_webhook_wrong_secret_is_404(client):
    r = client.post("/webhooks/telegram/nope", json=_tg_update("/start x"))
    assert r.status_code == 404


def test_toggle_and_disconnect(client):
    uid = _signup_login(client, "n4@example.com")
    from web.app.services.telegram import make_link_token
    client.post("/webhooks/telegram/dev-tg-secret",
                json=_tg_update(f"/start {make_link_token(uid)}", chat_id=42))
    assert _pref(uid).enabled

    # Unchecked checkbox sends no field -> disabled.
    client.post("/notifications/telegram/toggle", data={})
    assert _pref(uid).enabled is False

    client.post("/notifications/telegram/toggle", data={"enabled": "on"})
    assert _pref(uid).enabled is True

    client.post("/notifications/telegram/disconnect")
    pref = _pref(uid)
    assert pref.telegram_chat_id is None and pref.enabled is False


# The card's heading, which renders whenever the card does. Tests run without a
# bot token, so the connect button and the toggles never render — only this.
_CARD = "<h2>Telegram</h2>"


def test_telegram_card_is_hidden_from_non_admins(client):
    """Telegram is withdrawn from the public product for now: an ordinary
    account sees the email card only, and never an invitation to connect."""
    _signup_login(client, "tg-hidden@example.com")
    page = client.get("/notifications")
    assert page.status_code == 200
    assert _CARD not in page.text
    assert 'action="/notifications/telegram/' not in page.text
    assert 'action="/notifications/email/toggle"' in page.text  # email survives


def test_telegram_endpoints_404_for_non_admins(client):
    """Hiding the card is not the guard — the endpoints refuse it too, so a
    hand-rolled POST gets nowhere."""
    uid = _signup_login(client, "tg-guard@example.com")
    for path in ("toggle", "test", "disconnect"):
        r = client.post(f"/notifications/telegram/{path}", data={"enabled": "on"})
        assert r.status_code == 404, path
    assert _pref(uid) is None


def test_owner_still_sees_the_telegram_card(client):
    _login_owner(client)
    page = client.get("/notifications")
    assert page.status_code == 200 and _CARD in page.text


def test_already_linked_user_keeps_control_until_it_disconnects(client):
    """Nobody is left with alerts they can't turn off: an account that linked a
    chat before the feature was withdrawn keeps the card. Disconnecting is a
    one-way exit — the card goes for good."""
    uid = _signup_login(client, "tg-grandfathered@example.com")
    _link_chat(client, uid, chat_id=321)

    assert _CARD in client.get("/notifications").text
    r = client.post("/notifications/telegram/toggle", data={}, follow_redirects=False)
    assert r.status_code == 303 and _pref(uid).enabled is False

    r = client.post("/notifications/telegram/disconnect", follow_redirects=False)
    assert r.status_code == 303 and _pref(uid).telegram_chat_id is None

    assert _CARD not in client.get("/notifications").text
    assert client.post("/notifications/telegram/toggle", data={}).status_code == 404


def test_link_token_is_per_user(client):
    """A token minted for one user must never bind another user's session."""
    b_uid = _signup_login(client, "nb@example.com")
    from web.app.services.telegram import make_link_token
    b_token = make_link_token(b_uid)

    a_uid = _signup_login(client, "na@example.com")  # session now A
    client.post("/webhooks/telegram/dev-tg-secret",
                json=_tg_update(f"/start {b_token}", chat_id=77))

    # The chat binds to B (whom the token names), never to A.
    assert _pref(b_uid).telegram_chat_id == "77"
    assert _pref(a_uid) is None
