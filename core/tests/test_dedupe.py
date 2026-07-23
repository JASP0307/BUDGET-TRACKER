"""Tests for the content-signature duplicate guard."""

from datetime import date

from budgetcore.dedupe import signature


def test_identical_notifications_share_a_signature():
    # Two Popular notifications for one charge (different message ids) collide.
    a = signature("Popular VISA ISI *1111", date(2026, 7, 14), "PAYPAL *ALIPAYEU", 4520.39)
    b = signature("Popular VISA ISI *1111", date(2026, 7, 14), "PAYPAL *ALIPAYEU", 4520.39)
    assert a == b


def test_reversal_does_not_collide_with_charge():
    # A reversal is the opposite sign, so it must not be treated as a duplicate.
    charge = signature("Qik *3333", date(2026, 7, 14), "PAYPAL *ALIPAYEU", 4520.39)
    reversal = signature("Qik *3333", date(2026, 7, 14), "PAYPAL *ALIPAYEU", -4520.39)
    assert charge != reversal


def test_same_amount_different_card_is_distinct():
    popular = signature("Popular VISA ISI *1111", date(2026, 7, 14), "PAYPAL *ALIPAYEU", 4520.39)
    qik = signature("Qik *3333", date(2026, 7, 14), "PAYPAL *ALIPAYEU", 4520.39)
    assert popular != qik
