import pytest


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Rate-limit counters are process-global, so clear them around every test —
    otherwise logins in one test spend another test's budget."""
    from web.app.ratelimit import limiter
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient over a throwaway SQLite DB, startup/bootstrap included."""
    monkeypatch.setenv("BUDGET_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("BUDGET_WEBHOOK_SECRET", "testsecret")
    monkeypatch.setenv("BUDGET_USER_EMAIL", "test@example.com")
    monkeypatch.delenv("BUDGET_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("BUDGET_FERNET_KEY", raising=False)

    from web.app import db
    db.reset_engine()
    from fastapi.testclient import TestClient

    from web.app.main import app

    with TestClient(app) as c:
        yield c
    db.reset_engine()
