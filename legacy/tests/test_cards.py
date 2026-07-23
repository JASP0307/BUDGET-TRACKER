"""The card registry must keep labels byte-identical to the Sheet's rows —
they feed the dedupe signature, so a changed label would re-log old charges."""

from datetime import date

from budgetcore.models import Bank, Transaction, TxType
from budgettracker.cards import resolve_card


def _txn(bank, last4):
    return Transaction(
        message_id="m1", bank=bank, last4=last4, tx_type=TxType.CONSUMO,
        txn_date=date(2026, 7, 22), merchant="X", amount_dop=1.0,
        original_amount=1.0, currency="RD$")


def test_known_cards_get_historic_labels():
    assert resolve_card(_txn(Bank.POPULAR, "1111")).card == "Popular VISA ISI *1111"
    assert resolve_card(_txn(Bank.POPULAR, "2222")).card == "Popular Visa Débito Clásica *2222"
    assert resolve_card(_txn(Bank.QIK, "3333")).card == "Qik *3333"


def test_unknown_card_falls_back_to_generic_label():
    assert resolve_card(_txn(Bank.POPULAR, "1234")).card == "Popular *1234"
