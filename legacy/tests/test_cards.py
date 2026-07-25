"""Card labels come from config (`[cards.labels]`), not from the repo.

They must stay byte-identical to the Sheet's existing rows — they feed the dedupe
signature, so a changed label would re-log every old charge as new.
"""

from datetime import date

from budgetcore.models import Bank, Transaction, TxType
from budgettracker.cards import label_key, resolve_card

# Stand-in for a real deployment's config.toml [cards.labels] block.
LABELS = {
    "popular:1111": "Popular VISA ISI *1111",
    "popular:2222": "Popular Visa Débito Clásica *2222",
    "qik:3333": "Qik *3333",
}


def _txn(bank, last4):
    return Transaction(
        message_id="m1", bank=bank, last4=last4, tx_type=TxType.CONSUMO,
        txn_date=date(2025, 7, 22), merchant="X", amount_dop=1.0,
        original_amount=1.0, currency="RD$")


def test_label_key_matches_the_config_format():
    assert label_key(_txn(Bank.POPULAR, "1111")) == "popular:1111"
    assert label_key(_txn(Bank.QIK, "3333")) == "qik:3333"


def test_configured_cards_get_their_historic_labels():
    assert resolve_card(_txn(Bank.POPULAR, "1111"), LABELS).card == "Popular VISA ISI *1111"
    assert resolve_card(_txn(Bank.POPULAR, "2222"), LABELS).card == "Popular Visa Débito Clásica *2222"
    assert resolve_card(_txn(Bank.QIK, "3333"), LABELS).card == "Qik *3333"


def test_unconfigured_card_falls_back_to_generic_label():
    assert resolve_card(_txn(Bank.POPULAR, "9999"), LABELS).card == "Popular *9999"


def test_no_config_at_all_still_labels_every_card():
    """A fresh install has no [cards.labels]; it must not crash or blank out."""
    assert resolve_card(_txn(Bank.QIK, "3333")).card == "Qik *3333"
    assert resolve_card(_txn(Bank.POPULAR, "1111"), None).card == "Popular *1111"
