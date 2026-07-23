"""Content-signature duplicate guard.

Banks sometimes send two notification emails for one charge (different
message ids), so message-id dedupe alone is not enough. The signature keys a
transaction by its content; a reversal carries the opposite sign, so it never
collides with the original charge.
"""

from __future__ import annotations

from datetime import date


def signature(card: str, txn_date: date, merchant: str, signed_amount: float) -> tuple:
    """Content key: (card label, ISO date, merchant, rounded signed amount)."""
    return (card, txn_date.isoformat(), merchant, round(float(signed_amount), 2))
