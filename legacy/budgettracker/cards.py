"""Resolve (bank, last4) to this deployment's card display labels.

The single-user pipeline knows its three cards; the multi-user web app
resolves against each user's registered cards instead. Labels must stay
byte-identical to the Sheet's existing rows — they feed the dedupe signature.
"""

from __future__ import annotations

from dataclasses import replace

from budgetcore.models import Bank, Transaction

_LABELS = {
    (Bank.POPULAR, "1111"): "Popular VISA ISI *1111",
    (Bank.POPULAR, "2222"): "Popular Visa Débito Clásica *2222",
    (Bank.QIK, "3333"): "Qik *3333",
}


def resolve_card(txn: Transaction) -> Transaction:
    """Return a copy of `txn` with its display label set."""
    label = _LABELS.get((txn.bank, txn.last4))
    return replace(txn, card=label or txn.card_label)
