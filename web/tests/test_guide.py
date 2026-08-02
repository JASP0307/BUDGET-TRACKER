"""The public /help guide, the /static mount, and the optional clip slots."""

import pytest


def test_help_is_public(client):
    """Onboarding happens inside Gmail, so the explanation has to be linkable to
    someone who hasn't signed up — no redirect to /login."""
    r = client.get("/help", follow_redirects=False)
    assert r.status_code == 200
    assert "Cómo funciona" in r.text


def test_help_lists_supported_banks(client):
    html = client.get("/help").text
    for display in ("Banco Popular", "Qik", "Banco BHD", "Banreservas"):
        assert display in html


def test_help_is_translated(client):
    client.get("/lang/en")
    html = client.get("/help").text
    assert "How it works" in html
    assert "Cómo funciona" not in html


def test_static_is_mounted(client, tmp_path):
    from web.app.templating import STATIC_DIR
    probe = STATIC_DIR / "_probe.txt"
    probe.write_text("ok")
    try:
        r = client.get("/static/_probe.txt")
        assert r.status_code == 200 and r.text == "ok"
    finally:
        probe.unlink()


class TestClipSlots:
    """Recordings live outside git, so absence is the normal case and must not
    break a page."""

    @pytest.fixture()
    def clip_file(self):
        from web.app.templating import CLIP_DIR
        CLIP_DIR.mkdir(parents=True, exist_ok=True)
        made = []

        def make(name, suffix=".mp4"):
            p = CLIP_DIR / f"{name}{suffix}"
            p.write_bytes(b"\x00fake")
            made.append(p)
            return p

        yield make
        for p in made:
            p.unlink(missing_ok=True)

    def test_missing_clip_renders_nothing(self, client):
        from web.app.templating import clip
        assert clip("step1-forwarding") is None
        html = client.get("/help").text
        assert "<video" not in html

    def test_present_clip_is_offered(self, client, clip_file):
        from web.app.templating import clip
        clip_file("overview")
        assert clip("overview") == {"src": "/static/clips/overview.mp4",
                                    "poster": None}
        html = client.get("/help").text
        assert '<source src="/static/clips/overview.mp4"' in html
        assert "poster=" not in html

    def test_poster_is_used_when_it_exists(self, clip_file):
        from web.app.templating import clip
        clip_file("overview")
        clip_file("overview", ".jpg")
        assert clip("overview")["poster"] == "/static/clips/overview.jpg"

    def test_setup_step_clips_appear_only_once_filmed(self, client, clip_file):
        from web.app.auth.security import make_verify_token
        from web.app.db import get_sessionmaker
        from web.app.models import User
        from sqlalchemy import func, select
        client.post("/register", data={"email": "g1@example.com",
                                       "password": "supersecret"},
                    follow_redirects=True)
        with get_sessionmaker()() as s:
            uid = s.scalar(select(User).where(
                func.lower(User.email) == "g1@example.com")).id
        client.get(f"/verify?token={make_verify_token(uid)}", follow_redirects=True)
        client.post("/login", data={"email": "g1@example.com",
                                    "password": "supersecret"})

        assert "<video" not in client.get("/setup").text
        clip_file("step2-filter")
        html = client.get("/setup").text
        assert '<source src="/static/clips/step2-filter.mp4"' in html
        assert html.count("<video") == 1  # only the one that exists

    def test_clip_name_cannot_escape_the_static_dir(self):
        from web.app.templating import clip
        for name in ("../settings", "a/b", ".hidden", "..\\x"):
            assert clip(name) is None
