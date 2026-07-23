"""Environment-driven settings. Defaults suit local development (SQLite,
no encryption, no Telegram); production overrides via env / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str
    webhook_secret: str
    inbound_domain: str
    default_user_email: str
    fernet_key: str | None
    telegram_bot_token: str | None
    session_secret: str
    base_url: str
    postmark_token: str | None
    from_email: str
    bootstrap_password: str | None


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("BUDGET_DATABASE_URL", "sqlite:///web/dev.db"),
        webhook_secret=os.environ.get("BUDGET_WEBHOOK_SECRET", "dev-secret"),
        inbound_domain=os.environ.get("BUDGET_INBOUND_DOMAIN", "in.example.do"),
        default_user_email=os.environ.get("BUDGET_USER_EMAIL", "jabner0703@gmail.com"),
        fernet_key=os.environ.get("BUDGET_FERNET_KEY"),
        telegram_bot_token=os.environ.get("BUDGET_TELEGRAM_TOKEN"),
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
    )
