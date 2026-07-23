"""Map a transaction's merchant to a budget category."""

from __future__ import annotations

from .models import Transaction, TxType

UNCATEGORIZED = "Otros / sin categoría"


def categorize(txn: Transaction, rules: dict[str, str]) -> Transaction:
    """Return a copy of `txn` with `category` set.

    ATM withdrawals always map to "Retiro Efectivo". Otherwise the first
    case-insensitive merchant-substring rule wins; unmatched -> UNCATEGORIZED.
    """
    from dataclasses import replace

    if txn.tx_type is TxType.RETIRO:
        return replace(txn, category="Retiro Efectivo")

    merchant = txn.merchant.upper()
    for needle, category in rules.items():
        if needle.upper() in merchant:
            return replace(txn, category=category)
    return replace(txn, category=UNCATEGORIZED)


def is_uncategorized(txn: Transaction) -> bool:
    return txn.category == UNCATEGORIZED
