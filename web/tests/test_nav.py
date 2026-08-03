"""Header nav rendering.

Worth testing because nothing else catches a missing translation: `t()` falls
back to the Spanish source string, so an absent `_EN` entry renders Spanish in
the English UI instead of failing. test_i18n covers language *switching*, not
whether a given label was ever translated.
"""

import re

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


def _nav(client, path="/"):
    match = re.search(r"<nav>(.*?)</nav>", client.get(path).text, re.S)
    assert match, f"no <nav> rendered for {path}"
    return match.group(1)


def test_notifications_tab_is_in_the_nav_in_both_languages(client):
    _signup_login(client, "nav1@example.com")
    for lang, label in (("es", "Notificaciones"), ("en", "Notifications")):
        client.get(f"/lang/{lang}?next=/", follow_redirects=False)
        nav = _nav(client)
        assert 'href="/notifications"' in nav, f"[{lang}] no /notifications tab"
        assert label in nav, f"[{lang}] tab not translated (expected {label!r})"


def test_notifications_tab_marks_itself_active(client):
    _signup_login(client, "nav2@example.com")
    link = re.search(r'<a href="/notifications"[^>]*>', _nav(client, "/notifications"))
    assert link and 'aria-current="page"' in link.group(0)


def test_setup_alert_prompt_names_both_channels_for_the_owner(client):
    """The prompt used to offer Telegram only; email alerts now exist too and
    fire on a different trigger (threshold crossing, not every purchase). Both
    are named for accounts that can still see Telegram."""
    _login_owner(client)
    client.get("/lang/es?next=/setup", follow_redirects=False)
    es = client.get("/setup").text
    assert "o un correo solo cuando te pases del presupuesto" in es
    assert "Activa las alertas por Telegram" not in es

    client.get("/lang/en?next=/setup", follow_redirects=False)
    en = client.get("/setup").text
    assert "instant alert on every purchase" in en
    assert "or an email only when you go over budget" in en
    assert "Set up your alerts" in en


def test_setup_alert_prompt_offers_email_only_to_others(client):
    """Telegram is hidden from non-admins, so the wizard must not promise the
    per-purchase alert they can't turn on — only the email one."""
    _signup_login(client, "nav3@example.com")
    client.get("/lang/es?next=/setup", follow_redirects=False)
    es = client.get("/setup").text
    assert "¿Quieres un correo cuando te pases del presupuesto?" in es
    assert "aviso al instante en cada compra" not in es

    client.get("/lang/en?next=/setup", follow_redirects=False)
    en = client.get("/setup").text
    assert "Want an email when you go over budget?" in en
    assert "instant alert on every purchase" not in en
    assert "Set up your alerts" in en  # the link still goes to the email card
