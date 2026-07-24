"""The bank registry is the single source of truth; everything derives from it."""

from budgetcore import parsers
from budgetcore.banks import (SUPPORTED_BANKS, bank_choices, bank_domains,
                              from_query)


def test_every_registered_bank_has_a_parser():
    """A BankSpec with no parser wired would silently drop that bank's mail —
    fail loudly here instead so adding a bank can't half-land."""
    for spec in SUPPORTED_BANKS:
        assert spec.bank in parsers._PARSERS, f"no parser for {spec.bank}"


def test_derived_accessors_match_the_registry():
    assert bank_domains() == tuple(s.sender.split("@")[1] for s in SUPPORTED_BANKS)
    assert bank_choices() == [(s.bank.value, s.display_name) for s in SUPPORTED_BANKS]
    assert from_query() == " OR ".join(s.sender for s in SUPPORTED_BANKS)


def test_from_query_lists_every_sender():
    q = from_query()
    for spec in SUPPORTED_BANKS:
        assert spec.sender in q
