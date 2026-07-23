"""Admin panel: owner-only access, FX rates, and the email debug queue."""

from sqlalchemy import func, select


def _signup_login(client, email, pw="supersecret"):
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


def test_admin_requires_login(client):
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_admin_hidden_from_non_owner(client):
    _signup_login(client, "notadmin@example.com")
    assert client.get("/admin").status_code == 404
    # Actions are guarded too, not just the page.
    assert client.post("/admin/fx", data={"rate": "99",
                       "effective_date": "2026-07-01"}).status_code == 404


def test_admin_overview_loads_for_owner(client):
    _login_owner(client)
    r = client.get("/admin")
    assert r.status_code == 200 and "Admin" in r.text


def test_admin_bad_filter_defaults(client):
    _login_owner(client)
    assert client.get("/admin?status=bogus").status_code == 200


def test_add_fx_rate(client):
    _login_owner(client)
    client.post("/admin/fx", data={"rate": "61.50", "effective_date": "2026-07-01"})
    from web.app.db import get_sessionmaker
    from web.app.models import FxRate
    with get_sessionmaker()() as s:
        rate = s.scalar(select(FxRate))
        assert rate is not None and rate.rate == 61.5


def test_add_fx_rate_upserts_same_date(client):
    _login_owner(client)
    client.post("/admin/fx", data={"rate": "60", "effective_date": "2026-07-01"})
    client.post("/admin/fx", data={"rate": "62", "effective_date": "2026-07-01"})
    from web.app.db import get_sessionmaker
    from web.app.models import FxRate
    with get_sessionmaker()() as s:
        rows = s.scalars(select(FxRate)).all()
        assert len(rows) == 1 and rows[0].rate == 62


def test_email_detail_shows_body_and_delete(client):
    _login_owner(client)
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        raw = RawEmail(provider_message_id="f1", from_addr="x@bank.com",
                       subject="Fallo", html_body="cuerpo de prueba",
                       processing_status="failed", note="boom")
        s.add(raw)
        s.commit()
        rid = raw.id

    r = client.get(f"/admin/emails/{rid}")
    assert r.status_code == 200
    assert "cuerpo de prueba" in r.text and "boom" in r.text

    client.post(f"/admin/emails/{rid}/delete")
    with get_sessionmaker()() as s:
        assert s.get(RawEmail, rid) is None


def test_queue_filter_scopes_to_status(client):
    _login_owner(client)
    from web.app.db import get_sessionmaker
    from web.app.models import RawEmail
    with get_sessionmaker()() as s:
        s.add(RawEmail(provider_message_id="a", subject="ZZFailMail",
                       processing_status="failed"))
        s.add(RawEmail(provider_message_id="b", subject="ZZOkMail",
                       processing_status="processed"))
        s.commit()
    # Default 'problem' filter shows failed, not processed.
    body = client.get("/admin").text
    assert "ZZFailMail" in body and "ZZOkMail" not in body
    # 'all' shows both.
    body_all = client.get("/admin?status=all").text
    assert "ZZFailMail" in body_all and "ZZOkMail" in body_all
