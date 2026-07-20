"""Telegram notifications sent after each logged transaction."""

from __future__ import annotations

import requests

from .categorize import is_uncategorized
from .models import Transaction

_API = "https://api.telegram.org/bot{token}/sendMessage"


def send(bot_token: str, chat_id: str, text: str) -> None:
    resp = requests.post(
        _API.format(token=bot_token),
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()


def transaction_message(txn: Transaction, spent: float, budget: float) -> str:
    """Compose the per-transaction alert: what was charged + remaining budget."""
    remaining = budget - spent
    sign = "↩️ reversa " if txn.signed_amount() < 0 else ""
    lines = [
        f"{sign}<b>{txn.category}</b>",
        f"{txn.merchant} — RD${abs(txn.signed_amount()):,.2f} ({txn.card})",
    ]
    if budget > 0:
        pct = spent / budget * 100 if budget else 0
        if remaining < 0:
            lines.append(f"⚠️ <b>Excedido</b> por RD${-remaining:,.2f} "
                         f"(gastado RD${spent:,.2f} de RD${budget:,.2f})")
        else:
            lines.append(f"Restante: RD${remaining:,.2f} de RD${budget:,.2f} "
                         f"({pct:.0f}% usado)")
    if is_uncategorized(txn):
        lines.append("🗂️ Sin categoría — agrega una regla si se repite.")
    return "\n".join(lines)
