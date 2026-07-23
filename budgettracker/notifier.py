"""Telegram notifications sent after each logged transaction."""

from __future__ import annotations

import html
from datetime import date

import requests

from .categorize import is_uncategorized
from .models import Transaction

_API = "https://api.telegram.org/bot{token}/sendMessage"

_MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun",
              "jul", "ago", "sep", "oct", "nov", "dic"]


def send(bot_token: str, chat_id: str, text: str) -> None:
    resp = requests.post(
        _API.format(token=bot_token),
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()


def _progress_bar(pct: float, width: int = 10) -> str:
    filled = min(width, round(pct / 100 * width))
    return "▓" * filled + "░" * (width - filled)


def heartbeat_message(today_count: int, week_count: int, today: date) -> str:
    """Daily proof of life: a dead machine stops sending this, so silence no
    longer looks like a day without spending."""
    when = f"{today.day} {_MONTHS_ES[today.month - 1]}"
    txns = "transacción" if today_count == 1 else "transacciones"
    return (f"✅ <b>Tracker vivo</b> — {when}\n"
            f"Hoy: <b>{today_count}</b> {txns} · esta semana: <b>{week_count}</b>")


def transaction_message(txn: Transaction, spent: float, budget: float) -> str:
    """Compose the per-transaction alert: what was charged + remaining budget."""
    merchant = html.escape(txn.merchant)
    card = html.escape(txn.card)
    category = html.escape(txn.category or "Sin categoría")
    amount = abs(txn.signed_amount())
    when = f"{txn.txn_date.day} {_MONTHS_ES[txn.txn_date.month - 1]}"

    header = "↩️ Reversa" if txn.signed_amount() < 0 else "💳 Nuevo consumo"
    lines = [
        f"{header}",
        f"<b>{merchant}</b> — <b>RD${amount:,.2f}</b>",
        f"{card} · {when}",
        f"🏷️ {category}",
    ]

    if budget > 0:
        remaining = budget - spent
        pct = spent / budget * 100
        lines.append("")
        if remaining < 0:
            lines.append(f"🔴 <b>Presupuesto excedido</b> por RD${-remaining:,.2f}")
        else:
            status = "🟢" if pct < 70 else "🟡"
            lines.append(f"{status} Restante: <b>RD${remaining:,.2f}</b>")
        lines.append(f"{_progress_bar(min(pct, 100))} {pct:.0f}%")
        lines.append(f"RD${spent:,.2f} de RD${budget:,.2f} este mes")

    if is_uncategorized(txn):
        lines.append("")
        lines.append("🗂️ Sin categoría — agrega una regla si se repite.")
    return "\n".join(lines)
