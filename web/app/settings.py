"""Environment-driven settings. Defaults suit local development (SQLite,
no encryption, no Telegram); production overrides via env / .env.

Those dev defaults are convenient locally and dangerous in production: the
session secret signs both session cookies and password-reset tokens, so booting
on the published default would let anyone forge a session for any account. So
they fail *closed* — with BUDGET_ENV=production, `require_production_secrets`
refuses to start the app until every secret is real. See main._lifespan.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

PRODUCTION = "production"

# The placeholder values below. Committed to this repo, therefore public;
# reaching production with any of them is a boot-blocking error.
DEV_DEFAULTS = {
    "webhook_secret": "dev-secret",
    "telegram_webhook_secret": "dev-tg-secret",
    "session_secret": "dev-session-secret-change-me",
}
# Long enough that guessing is hopeless; `openssl rand -hex 32` gives 64.
MIN_SECRET_LENGTH = 32


class InsecureConfiguration(RuntimeError):
    """Production configuration that would expose user data. Raised at startup,
    before the app serves anything, so a bad deploy fails loudly."""


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str
    webhook_secret: str
    inbound_domain: str
    default_user_email: str
    fernet_key: str | None
    telegram_bot_token: str | None
    telegram_bot_username: str | None
    telegram_webhook_secret: str
    session_secret: str
    base_url: str
    postmark_token: str | None
    from_email: str
    bootstrap_password: str | None
    postmark_inbound_address: str | None
    ollama_url: str
    ollama_model: str

    @property
    def is_production(self) -> bool:
        return self.env == PRODUCTION


def get_settings() -> Settings:
    return Settings(
        # "production" turns on the startup secret check (below), Secure session
        # cookies, and the required-Fernet-key rule. Anything else is dev.
        env=os.environ.get("BUDGET_ENV", "development").strip().lower(),
        database_url=os.environ.get("BUDGET_DATABASE_URL", "sqlite:///web/dev.db"),
        webhook_secret=os.environ.get("BUDGET_WEBHOOK_SECRET", "dev-secret"),
        inbound_domain=os.environ.get("BUDGET_INBOUND_DOMAIN", "in.example.do"),
        default_user_email=os.environ.get("BUDGET_USER_EMAIL", "jabner0703@gmail.com"),
        fernet_key=os.environ.get("BUDGET_FERNET_KEY"),
        telegram_bot_token=os.environ.get("BUDGET_TELEGRAM_TOKEN"),
        # Bot @username for building t.me deep links; discovered via getMe if unset.
        telegram_bot_username=os.environ.get("BUDGET_TELEGRAM_BOT_USERNAME"),
        # Guards the Telegram webhook URL, like BUDGET_WEBHOOK_SECRET guards Postmark's.
        telegram_webhook_secret=os.environ.get("BUDGET_TELEGRAM_WEBHOOK_SECRET", "dev-tg-secret"),
        # Signs session cookies and email-verification tokens. The dev default
        # keeps local runs working; production MUST override it.
        session_secret=os.environ.get("BUDGET_SESSION_SECRET", "dev-session-secret-change-me"),
        # Absolute origin used to build links in outbound email.
        base_url=os.environ.get("BUDGET_BASE_URL", "http://localhost:8000").rstrip("/"),
        # Postmark outbound (verification/reset mail). Absent → links are logged.
        postmark_token=os.environ.get("BUDGET_POSTMARK_TOKEN"),
        from_email=os.environ.get("BUDGET_FROM_EMAIL", "no-reply@localhost"),
        # If set, the bootstrap user gets this password so the dev can log in.
        bootstrap_password=os.environ.get("BUDGET_BOOTSTRAP_PASSWORD"),
        # Postmark's server inbound address (e.g. <hash>@inbound.postmarkapp.com).
        # When set, per-user forward addresses use its mailbox-hash form instead
        # of a custom inbound domain. See services.inbound.format_inbound_address.
        postmark_inbound_address=os.environ.get("BUDGET_POSTMARK_INBOUND_ADDRESS"),
        # Local LLM (Ollama) for category suggestions. Not secrets. Setting
        # BUDGET_OLLAMA_URL to an empty string disables the suggestion sweep.
        ollama_url=os.environ.get(
            "BUDGET_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=os.environ.get("BUDGET_OLLAMA_MODEL", "qwen2.5:3b"),
    )


# (attribute, env var) for every secret that must be real in production.
_REQUIRED_SECRETS = (
    ("session_secret", "BUDGET_SESSION_SECRET"),
    ("webhook_secret", "BUDGET_WEBHOOK_SECRET"),
    ("telegram_webhook_secret", "BUDGET_TELEGRAM_WEBHOOK_SECRET"),
)


def require_production_secrets(settings: Settings | None = None) -> None:
    """Raise `InsecureConfiguration` if a production deployment is misconfigured.

    Checks every problem before raising, so one restart tells the operator
    everything that is wrong instead of one item per attempt. A no-op outside
    production.
    """
    settings = settings or get_settings()
    if not settings.is_production:
        return

    problems: list[str] = []
    for attr, env_var in _REQUIRED_SECRETS:
        value = getattr(settings, attr) or ""
        if not value:
            problems.append(f"{env_var} is not set")
        elif value == DEV_DEFAULTS.get(attr):
            problems.append(f"{env_var} is still the development default")
        elif len(value) < MIN_SECRET_LENGTH:
            problems.append(
                f"{env_var} is shorter than {MIN_SECRET_LENGTH} characters")

    # Without a key, crypto.encrypt silently stores bank email bodies as
    # plaintext (see crypto.py) — acceptable in dev, never with real users.
    if not settings.fernet_key:
        problems.append(
            "BUDGET_FERNET_KEY is not set, so stored email bodies would be "
            "plaintext")

    # Session cookies are Secure-only in production; an http:// origin would
    # mean verification and reset links that can't carry a login.
    if not settings.base_url.startswith("https://"):
        problems.append(f"BUDGET_BASE_URL must be https:// (got {settings.base_url!r})")

    if problems:
        raise InsecureConfiguration(
            "Refusing to start with BUDGET_ENV=production:\n  - "
            + "\n  - ".join(problems))
