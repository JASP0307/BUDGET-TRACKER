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


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("BUDGET_DATABASE_URL", "sqlite:///web/dev.db"),
        webhook_secret=os.environ.get("BUDGET_WEBHOOK_SECRET", "dev-secret"),
        inbound_domain=os.environ.get("BUDGET_INBOUND_DOMAIN", "in.example.do"),
        default_user_email=os.environ.get("BUDGET_USER_EMAIL", "jabner0703@gmail.com"),
        fernet_key=os.environ.get("BUDGET_FERNET_KEY"),
        telegram_bot_token=os.environ.get("BUDGET_TELEGRAM_TOKEN"),
    )
