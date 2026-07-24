"""Language toggle: cookie switches the UI, and persists to the account."""

from sqlalchemy import func, select


def _verified_login(client, email, pw="supersecret"):
    from web.app.auth.security import make_verify_token
    from web.app.db import get_sessionmaker
    from web.app.models import User
    client.post("/register", data={"email": email, "password": pw},
                follow_redirects=True)
    with get_sessionmaker()() as s:
        uid = s.scalar(select(User).where(func.lower(User.email) == email.lower())).id
    client.get(f"/verify?token={make_verify_token(uid)}", follow_redirects=True)
    client.post("/login", data={"email": email, "password": pw})
    return uid


def test_default_language_is_spanish(client):
    assert "Iniciar sesión" in client.get("/login").text


def test_lang_cookie_switches_ui_to_english(client):
    client.get("/lang/en")  # sets the lang cookie in the test client's jar
    body = client.get("/login").text
    assert "Sign in" in body
    assert "Iniciar sesión" not in body


def test_invalid_lang_falls_back_to_spanish(client):
    client.get("/lang/zz")
    assert "Iniciar sesión" in client.get("/login").text


def test_lang_persists_to_account_when_signed_in(client):
    from web.app.db import get_sessionmaker
    from web.app.models import User
    uid = _verified_login(client, "lang1@example.com")
    client.get("/lang/en")
    with get_sessionmaker()() as s:
        assert s.get(User, uid).locale == "en"


def test_login_seeds_cookie_from_saved_language(client):
    from web.app.db import get_sessionmaker
    from web.app.models import User
    uid = _verified_login(client, "lang2@example.com")
    with get_sessionmaker()() as s:
        s.get(User, uid).locale = "en"
        s.commit()
    # Fresh login should read the saved locale and set the cookie accordingly.
    client.cookies.clear()
    client.post("/login", data={"email": "lang2@example.com",
                                "password": "supersecret"})
    assert client.cookies.get("lang") == "en"


def test_lang_route_only_redirects_locally(client):
    # An external next target must not be honored (no open redirect).
    r = client.get("/lang/en", params={"next": "https://evil.example/x"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
