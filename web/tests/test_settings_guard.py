"""The production configuration guard: a deployment missing a real secret must
refuse to boot rather than serve on the dev defaults published in this repo."""

import pytest

GOOD = "f" * 64


def _prod_env(monkeypatch, **overrides):
    env = {
        "BUDGET_ENV": "production",
        "BUDGET_SESSION_SECRET": GOOD,
        "BUDGET_WEBHOOK_SECRET": GOOD,
        "BUDGET_TELEGRAM_WEBHOOK_SECRET": GOOD,
        "BUDGET_FERNET_KEY": GOOD,
        "BUDGET_BASE_URL": "https://budget.example.do",
    }
    env.update(overrides)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_fully_configured_production_passes(monkeypatch):
    from web.app.settings import require_production_secrets
    _prod_env(monkeypatch)
    require_production_secrets()  # no raise


def test_development_never_raises(monkeypatch):
    """Dev defaults are the point of dev — the guard must stay out of the way."""
    from web.app.settings import require_production_secrets
    monkeypatch.setenv("BUDGET_ENV", "development")
    for key in ("BUDGET_SESSION_SECRET", "BUDGET_WEBHOOK_SECRET",
                "BUDGET_TELEGRAM_WEBHOOK_SECRET", "BUDGET_FERNET_KEY"):
        monkeypatch.delenv(key, raising=False)
    require_production_secrets()  # no raise


@pytest.mark.parametrize("env_var", [
    "BUDGET_SESSION_SECRET",
    "BUDGET_WEBHOOK_SECRET",
    "BUDGET_TELEGRAM_WEBHOOK_SECRET",
    "BUDGET_FERNET_KEY",
])
def test_missing_secret_blocks_startup(monkeypatch, env_var):
    from web.app.settings import InsecureConfiguration, require_production_secrets
    _prod_env(monkeypatch, **{env_var: None})
    with pytest.raises(InsecureConfiguration, match=env_var):
        require_production_secrets()


@pytest.mark.parametrize("env_var,dev_default", [
    ("BUDGET_SESSION_SECRET", "dev-session-secret-change-me"),
    ("BUDGET_WEBHOOK_SECRET", "dev-secret"),
    ("BUDGET_TELEGRAM_WEBHOOK_SECRET", "dev-tg-secret"),
])
def test_dev_default_blocks_startup(monkeypatch, env_var, dev_default):
    from web.app.settings import InsecureConfiguration, require_production_secrets
    _prod_env(monkeypatch, **{env_var: dev_default})
    with pytest.raises(InsecureConfiguration, match="development default"):
        require_production_secrets()


def test_short_secret_blocks_startup(monkeypatch):
    from web.app.settings import InsecureConfiguration, require_production_secrets
    _prod_env(monkeypatch, BUDGET_SESSION_SECRET="tooshort")
    with pytest.raises(InsecureConfiguration, match="shorter than"):
        require_production_secrets()


def test_plaintext_base_url_blocks_startup(monkeypatch):
    from web.app.settings import InsecureConfiguration, require_production_secrets
    _prod_env(monkeypatch, BUDGET_BASE_URL="http://budget.example.do")
    with pytest.raises(InsecureConfiguration, match="https"):
        require_production_secrets()


def test_all_problems_reported_at_once(monkeypatch):
    """One restart should tell the operator everything that's wrong."""
    from web.app.settings import InsecureConfiguration, require_production_secrets
    _prod_env(monkeypatch, BUDGET_SESSION_SECRET=None, BUDGET_FERNET_KEY=None,
              BUDGET_BASE_URL="http://budget.example.do")
    with pytest.raises(InsecureConfiguration) as exc:
        require_production_secrets()
    message = str(exc.value)
    assert "BUDGET_SESSION_SECRET" in message
    assert "BUDGET_FERNET_KEY" in message
    assert "BUDGET_BASE_URL" in message


def test_session_cookie_is_secure_in_production(monkeypatch):
    """The Secure flag is what keeps the session off a plaintext request."""
    from starlette.middleware.sessions import SessionMiddleware
    _prod_env(monkeypatch)
    from web.app.main import create_app
    app = create_app()
    session_mw = [m for m in app.user_middleware if m.cls is SessionMiddleware]
    assert session_mw, "SessionMiddleware not installed"
    assert session_mw[0].kwargs["https_only"] is True


def test_session_cookie_is_not_secure_in_development(monkeypatch):
    from starlette.middleware.sessions import SessionMiddleware
    monkeypatch.setenv("BUDGET_ENV", "development")
    from web.app.main import create_app
    app = create_app()
    session_mw = [m for m in app.user_middleware if m.cls is SessionMiddleware]
    assert session_mw[0].kwargs["https_only"] is False
