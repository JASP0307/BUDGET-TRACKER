"""Auth flow: registration, email verification, login gating, and that one
account cannot touch another's data."""

from sqlalchemy import func, select


def _register(client, email, password="supersecret"):
    return client.post("/register", data={"email": email, "password": password},
                       follow_redirects=True)


def _user_id(email):
    from web.app.db import get_sessionmaker
    from web.app.models import User
    with get_sessionmaker()() as s:
        return s.scalar(select(User).where(func.lower(User.email) == email.lower())).id


def _verify(client, email):
    from web.app.auth.security import make_verify_token
    token = make_verify_token(_user_id(email))
    return client.get(f"/verify?token={token}", follow_redirects=True)


def test_dashboard_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_register_verify_login_flow(client):
    r = _register(client, "nuevo@example.com")
    assert r.status_code == 200 and "Revisa tu correo" in r.text

    # Unverified: login is refused.
    r = client.post("/login", data={"email": "nuevo@example.com",
                                    "password": "supersecret"})
    assert "Confirma tu correo" in r.text

    assert "confirmado" in _verify(client, "nuevo@example.com").text.lower()

    # Verified: login succeeds and the dashboard loads.
    r = client.post("/login", data={"email": "nuevo@example.com",
                                    "password": "supersecret"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/").status_code == 200


def test_login_wrong_password(client):
    _register(client, "x@example.com")
    _verify(client, "x@example.com")
    r = client.post("/login", data={"email": "x@example.com", "password": "nope"})
    assert "incorrectos" in r.text


def test_duplicate_email_rejected(client):
    _register(client, "dup@example.com")
    r = _register(client, "dup@example.com")
    assert "Ya existe una cuenta" in r.text


def test_invalid_verification_token(client):
    r = client.get("/verify?token=garbage", follow_redirects=True)
    assert "inválido" in r.text.lower() or "vencido" in r.text.lower()


def test_bad_email_and_short_password_rejected(client):
    assert "correo válido" in client.post(
        "/register", data={"email": "notanemail", "password": "supersecret"}).text
    assert "al menos" in client.post(
        "/register", data={"email": "ok@example.com", "password": "short"}).text


def test_accounts_are_isolated(client):
    """A logged-in user must not be able to delete another user's rule."""
    from web.app.db import get_sessionmaker
    from web.app.models import Category, Rule

    _register(client, "a@example.com")
    _verify(client, "a@example.com")
    _register(client, "b@example.com")
    _verify(client, "b@example.com")

    b_id = _user_id("b@example.com")
    with get_sessionmaker()() as s:
        cat = s.scalar(select(Category).where(Category.user_id == b_id).limit(1))
        rule = Rule(user_id=b_id, substring="SECRETO", category_id=cat.id)
        s.add(rule)
        s.commit()
        rule_id = rule.id

    # Log in as A, then try to delete B's rule.
    client.post("/login", data={"email": "a@example.com", "password": "supersecret"})
    client.post(f"/rules/{rule_id}/delete", follow_redirects=False)

    with get_sessionmaker()() as s:
        assert s.get(Rule, rule_id) is not None  # untouched
