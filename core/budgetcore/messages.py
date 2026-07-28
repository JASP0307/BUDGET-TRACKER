"""Notification message builders (Spanish, Telegram-style HTML).

Pure string functions — no transport. Callers decide the channel
(Telegram today; web push / in-app later).
"""

from __future__ import annotations

import html
from datetime import date

from .categorize import is_uncategorized
from .models import Transaction

_MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun",
              "jul", "ago", "sep", "oct", "nov", "dic"]


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


def sweep_message(calls: int, created: int, elapsed: float) -> str:
    """The category-suggestion sweep just hit the local model — flags real CPU
    load on the machine running Ollama, since a run with nothing to ask never
    calls this."""
    merchants = "comercio" if calls == 1 else "comercios"
    suggs = "sugerencia nueva" if created == 1 else "sugerencias nuevas"
    return (f"🤖 <b>Sugerencias de categoría</b>\n"
            f"{calls} {merchants} consultados ({elapsed:.0f}s) · "
            f"{created} {suggs}")


def transaction_message(txn: Transaction, spent: float, budget: float) -> str:
    """Compose the per-transaction alert: what was charged + remaining budget."""
    merchant = html.escape(txn.merchant)
    card = html.escape(txn.card_label)
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
