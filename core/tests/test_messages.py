"""Tests for the notification message builders."""

from datetime import date

from budgetcore.messages import heartbeat_message, transaction_message
from budgetcore.models import Bank, Transaction, TxType

TODAY = date(2026, 7, 22)


def _txn(**overrides):
    base = dict(
        message_id="m1", bank=Bank.QIK, last4="3333", tx_type=TxType.CONSUMO,
        txn_date=TODAY, merchant="AMAZON 1", amount_dop=500.0,
        original_amount=500.0, currency="RD$", category="Otros / sin categoría",
        card="Qik *3333",
    )
    base.update(overrides)
    return Transaction(**base)


def test_heartbeat_singular_and_plural():
    single = heartbeat_message(1, 5, TODAY)
    assert "1</b> transacción" in single
    plural = heartbeat_message(3, 12, TODAY)
    assert "3</b> transacciones" in plural
    assert "22 jul" in plural
    assert "Tracker vivo" in plural
    assert "12" in plural


def test_transaction_message_contents():
    text = transaction_message(_txn(), spent=800.0, budget=2000.0)
    assert "Nuevo consumo" in text
    assert "AMAZON 1" in text
    assert "Qik *3333" in text
    assert "RD$1,200.00" in text          # remaining
    assert "agrega una regla" in text     # uncategorized hint


def test_transaction_message_unlabeled_card_falls_back():
    text = transaction_message(_txn(card=None), spent=0.0, budget=0.0)
    assert "Qik *3333" in text  # card_label fallback from (bank, last4)


def test_reversal_header_and_exceeded_budget():
    text = transaction_message(
        _txn(tx_type=TxType.REVERSAL), spent=2500.0, budget=2000.0)
    assert "Reversa" in text
    assert "excedido" in text
