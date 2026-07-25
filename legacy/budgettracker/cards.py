"""Resolve (bank, last4) to this deployment's card display labels.

The single-user pipeline knows its own cards; the multi-user web app resolves
against each user's registered cards instead. Labels must stay byte-identical to
the Sheet's existing rows — they feed the dedupe signature, so a changed label
would re-log every old charge as new.

The mapping therefore lives in `config.toml` (`[cards.labels]`, git-ignored)
rather than in this file: it is deployment-specific data, and a card's last four
digits are personal enough not to belong in a public repo. With no mapping the
label falls back to the generic `Bank *last4` form — right for a fresh install,
but on a deployment whose Sheet already holds labelled rows it *would* change the
dedupe signature, so keep the real mapping in config.
"""

from __future__ import annotations

from dataclasses import replace

from budgetcore.models import Transaction


def label_key(txn: Transaction) -> str:
    """The `[cards.labels]` key for a transaction: "<bank>:<last4>"."""
    return f"{txn.bank.value}:{txn.last4}"


def resolve_card(txn: Transaction,
                 labels: dict[str, str] | None = None) -> Transaction:
    """Return a copy of `txn` with its display label set from `labels`."""
    label = (labels or {}).get(label_key(txn))
    return replace(txn, card=label or txn.card_label)
