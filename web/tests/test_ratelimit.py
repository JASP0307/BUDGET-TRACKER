"""Rate limiting on the auth endpoints: password guessing and mail flooding."""

from sqlalchemy import func, select

from web.app import ratelimit


def _user_id(email):
    from web.app.db import get_sessionmaker
    from web.app.models import User
    with get_sessionmaker()() as s:
        return s.scalar(select(User).where(
            func.lower(User.email) == email.lower())).id


def _signup(client, email, pw="supersecret"):
    from web.app.auth.security import make_verify_token
    client.post("/register", data={"email": email, "password": pw},
                follow_redirects=True)
    client.get(f"/verify?token={make_verify_token(_user_id(email))}",
               follow_redirects=True)
    ratelimit.limiter.reset()  # registration spent part of the mail budget


# ---- the limiter itself ----

def test_limiter_allows_up_to_the_cap_then_refuses():
    limiter = ratelimit.RateLimiter()
    limit = ratelimit.Limit(max_hits=3, window_seconds=60)
    assert [limiter.allow("k", limit, now=0) for _ in range(3)] == [True] * 3
    assert limiter.allow("k", limit, now=0) is False


def test_limiter_window_rolls_forward():
    limiter = ratelimit.RateLimiter()
    limit = ratelimit.Limit(max_hits=2, window_seconds=60)
    assert limiter.allow("k", limit, now=0) is True
    assert limiter.allow("k", limit, now=10) is True
    assert limiter.allow("k", limit, now=20) is False
    # Once the first two hits age out, the caller is allowed again.
    assert limiter.allow("k", limit, now=71) is True


def test_limiter_keys_are_independent():
    limiter = ratelimit.RateLimiter()
    limit = ratelimit.Limit(max_hits=1, window_seconds=60)
    assert limiter.allow("a", limit, now=0) is True
    assert limiter.allow("a", limit, now=0) is False
    assert limiter.allow("b", limit, now=0) is True


def test_refused_attempts_do_not_extend_the_penalty():
    """A blocked caller retrying must not push their own window forward."""
    limiter = ratelimit.RateLimiter()
    limit = ratelimit.Limit(max_hits=1, window_seconds=60)
    limiter.allow("k", limit, now=0)
    for t in range(1, 60):
        limiter.allow("k", limit, now=t)
    assert limiter.allow("k", limit, now=61) is True


def test_client_ip_prefers_the_last_forwarded_entry():
    """The first XFF entry is client-controlled; the proxy appends the real one."""
    class _Req:
        headers = {"x-forwarded-for": "1.2.3.4, 203.0.113.9"}
        client = None
    assert ratelimit.client_ip(_Req()) == "203.0.113.9"


# ---- wired into the endpoints ----

def test_login_throttled_after_repeated_failures(client):
    _signup(client, "brute@example.com")
    email = "brute@example.com"

    # LOGIN_PER_EMAIL (5) trips first.
    for _ in range(ratelimit.LOGIN_PER_EMAIL.max_hits):
        r = client.post("/login", data={"email": email, "password": "wrong"})
        assert "incorrectos" in r.text

    blocked = client.post("/login", data={"email": email, "password": "wrong"})
    assert "Demasiados intentos" in blocked.text

    # And the correct password is refused too while throttled — otherwise the
    # limit wouldn't actually stop a guessing run that eventually gets it right.
    still = client.post("/login", data={"email": email,
                                        "password": "supersecret"},
                        follow_redirects=False)
    assert still.status_code == 200 and "Demasiados intentos" in still.text


def test_forgot_stops_sending_mail_when_throttled(client, monkeypatch):
    _signup(client, "flood@example.com")
    sent = []
    monkeypatch.setattr("web.app.auth.router.send_password_reset_email",
                        lambda to, url: sent.append(to))

    for _ in range(ratelimit.EMAIL_PER_EMAIL.max_hits + 3):
        r = client.post("/forgot", data={"email": "flood@example.com"})
        # The response never changes — it must not reveal the limit or whether
        # the account exists.
        assert "Si existe una cuenta" in r.text

    assert len(sent) == ratelimit.EMAIL_PER_EMAIL.max_hits


def test_register_throttled_after_repeated_signups(client):
    for i in range(ratelimit.EMAIL_PER_IP.max_hits):
        r = client.post("/register", data={"email": f"s{i}@example.com",
                                          "password": "supersecret"},
                        follow_redirects=True)
        assert "Revisa tu correo" in r.text

    blocked = client.post("/register", data={"email": "s99@example.com",
                                             "password": "supersecret"},
                          follow_redirects=True)
    assert "Demasiados intentos" in blocked.text

    # And no account was created for the blocked attempt.
    from web.app.db import get_sessionmaker
    from web.app.models import User
    with get_sessionmaker()() as s:
        assert s.scalar(select(User).where(
            User.email == "s99@example.com")) is None


def test_invalid_registration_does_not_spend_the_mail_budget(client):
    """A typo'd email shouldn't cost a real visitor on the same IP a signup."""
    for _ in range(ratelimit.EMAIL_PER_IP.max_hits + 2):
        r = client.post("/register", data={"email": "not-an-email",
                                          "password": "supersecret"},
                        follow_redirects=True)
        assert "correo válido" in r.text

    ok = client.post("/register", data={"email": "real@example.com",
                                        "password": "supersecret"},
                     follow_redirects=True)
    assert "Revisa tu correo" in ok.text
