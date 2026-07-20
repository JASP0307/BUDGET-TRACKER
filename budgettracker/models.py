"""Core data types shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class TxType(str, Enum):
    CONSUMO = "consumo"        # card purchase
    RETIRO = "retiro"         # ATM cash withdrawal
    REVERSAL = "reversal"     # reversed/refunded charge (negative amount)


@dataclass(frozen=True)
class Transaction:
    """One normalized transaction extracted from a notification email."""

    message_id: str            # Gmail message id — the dedupe key
    card: str                  # e.g. "Popular VISA ISI *1111"
    tx_type: TxType
    txn_date: date
    merchant: str              # merchant, ATM name, or reversal source
    amount_dop: float          # amount in RD$, already FX-converted, signed
    original_amount: float     # amount as printed on the email
    currency: str              # "RD$" or "US$"
    category: str | None = None  # filled in by categorize step

    def signed_amount(self) -> float:
        """Reversals count as negative so a month self-corrects."""
        if self.tx_type is TxType.REVERSAL:
            return -abs(self.amount_dop)
        return abs(self.amount_dop)
