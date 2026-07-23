"""Telegram transport. Message text is built in budgetcore.messages."""

from __future__ import annotations

import requests

# Re-exported so callers keep addressing notifier.* for text + transport.
from budgetcore.messages import heartbeat_message, transaction_message  # noqa: F401

_API = "https://api.telegram.org/bot{token}/sendMessage"


def send(bot_token: str, chat_id: str, text: str) -> None:
    resp = requests.post(
        _API.format(token=bot_token),
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()
