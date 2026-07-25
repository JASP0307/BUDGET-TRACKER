"""Load config.toml (see config.example.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    usd_to_dop: float
    fetch_window: str
    gmail_credentials_file: str
    gmail_token_file: str
    spreadsheet_id: str
    transactions_tab: str
    budget_tab: str
    telegram_bot_token: str
    telegram_chat_id: str
    rules: dict[str, str]
    budget: dict[str, float]
    # "<bank>:<last4>" -> Sheet display label. Deployment-specific and personal,
    # so it lives here rather than in the repo. See cards.resolve_card.
    card_labels: dict[str, str]


def load(path: str | Path = "config.toml") -> Config:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    g, gm, sh, tg = data["general"], data["gmail"], data["sheets"], data["telegram"]
    return Config(
        usd_to_dop=float(g["usd_to_dop"]),
        fetch_window=g["fetch_window"],
        gmail_credentials_file=gm["credentials_file"],
        gmail_token_file=gm["token_file"],
        spreadsheet_id=sh["spreadsheet_id"],
        transactions_tab=sh["transactions_tab"],
        budget_tab=sh["budget_tab"],
        telegram_bot_token=tg["bot_token"],
        telegram_chat_id=tg["chat_id"],
        rules=dict(data.get("categorize", {}).get("rules", {})),
        budget={k: float(v) for k, v in data.get("budget", {}).items()},
        card_labels={str(k): str(v) for k, v in
                     data.get("cards", {}).get("labels", {}).items()},
    )
