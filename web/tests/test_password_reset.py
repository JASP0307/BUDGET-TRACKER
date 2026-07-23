"""Password reset: request flow, redeeming a link, single-use enforcement,
and that the notice never reveals whether an email exists."""

from sqlalchemy import func, select


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
    return uid


def _reset_token(email):
    from web.app.auth.security import make_reset_token
    u = _user(email)
    return make_reset_token(u.id, u.hashed_password)


def test_forgot_page_loads(client):
    assert client.get("/forgot").status_code == 200


def test_forgot_unknown_email_shows_generic_notice(client):
    r = client.post("/forgot", data={"email": "nobody@example.com"})
    assert r.status_code == 200 and "Si existe una cuenta" in r.text


def test_reset_flow_changes_password(client):
    _signup(client, "r1@example.com")
    token = _reset_token("r1@example.com")

    assert client.get(f"/reset?token={token}").status_code == 200

    r = client.post("/reset", data={"token": token, "password": "brandnewpass"})
    assert "actualizada" in r.text.lower()

    # New password works; the old one no longer does.
    ok = client.post("/login", data={"email": "r1@example.com",
                                     "password": "brandnewpass"},
                     follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    client.post("/logout")
    bad = client.post("/login", data={"email": "r1@example.com",
                                      "password": "supersecret"})
    assert "incorrectos" in bad.text


def test_reset_token_is_single_use(client):
    _signup(client, "r2@example.com")
    token = _reset_token("r2@example.com")
    client.post("/reset", data={"token": token, "password": "firstchange1"})

    # The same link no longer works — its fingerprint is stale after the change.
    again = client.post("/reset", data={"token": token, "password": "secondchange1"})
    assert "inválido" in again.text.lower() or "vencido" in again.text.lower()


def test_garbage_reset_token_rejected(client):
    r = client.get("/reset?token=garbage", follow_redirects=True)
    assert "inválido" in r.text.lower() or "vencido" in r.text.lower()


def test_verify_token_is_not_a_reset_token(client):
    """Salt separation: an email-verification token can't reset a password."""
    uid = _signup(client, "r3@example.com")
    from web.app.auth.security import make_verify_token
    verify_token = make_verify_token(uid)
    r = client.get(f"/reset?token={verify_token}", follow_redirects=True)
    assert "inválido" in r.text.lower() or "vencido" in r.text.lower()


def test_short_new_password_rejected(client):
    _signup(client, "r4@example.com")
    token = _reset_token("r4@example.com")
    r = client.post("/reset", data={"token": token, "password": "short"})
    assert "al menos" in r.text
    # Original password still valid.
    ok = client.post("/login", data={"email": "r4@example.com",
                                     "password": "supersecret"},
                     follow_redirects=False)
    assert ok.status_code == 303
