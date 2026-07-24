"""Core data types shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class Bank(str, Enum):
    POPULAR = "popular"
    QIK = "qik"
    BHD = "bhd"


class TxType(str, Enum):
    CONSUMO = "consumo"        # card purchase
    RETIRO = "retiro"         # ATM cash withdrawal
    REVERSAL = "reversal"     # reversed/refunded charge (negative amount)


@dataclass(frozen=True)
class Transaction:
    """One normalized transaction extracted from a notification email.

    Parsers identify the card only as (bank, last4); the human-readable
    `card` label is resolved afterwards by the caller against its own card
    registry (legacy: a hardcoded map; web: the user's registered cards).
    """

    message_id: str            # source message id — the primary dedupe key
    bank: Bank
    last4: str                 # last 4 digits, "????" if absent from the email
    tx_type: TxType
    txn_date: date
    merchant: str              # merchant, ATM name, or reversal source
    amount_dop: float          # amount in RD$, already FX-converted, signed
    original_amount: float     # amount as printed on the email
    currency: str              # "RD$" or "US$"
    category: str | None = None  # filled in by categorize step
    card: str | None = None    # display label, e.g. "Popular VISA ISI *1111"

    @property
    def card_label(self) -> str:
        """Resolved label, falling back to a generic bank+last4 form."""
        return self.card or f"{self.bank.value.capitalize()} *{self.last4}"

    def signed_amount(self) -> float:
        """Reversals count as negative so a month self-corrects."""
        if self.tx_type is TxType.REVERSAL:
            return -abs(self.amount_dop)
        return abs(self.amount_dop)
