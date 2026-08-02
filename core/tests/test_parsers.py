"""Parser tests against fixtures derived from real notification emails."""

from datetime import date
from pathlib import Path

import pytest

from budgetcore.categorize import categorize
from budgetcore.models import Bank, TxType
from budgetcore.parsers import parse_email

FIXTURES = Path(__file__).parent / "fixtures"
RATE = 60.0

POPULAR = "notificaciones@popularenlinea.com"
QIK = "notificaciones@qik.do"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_popular_consumo_rdollar():
    txn = parse_email("m1", POPULAR, "Notificación de Consumo",
                      _load("popular_consumo.html"), usd_to_dop=RATE)
    assert txn is not None
    assert txn.tx_type is TxType.CONSUMO
    assert txn.bank is Bank.POPULAR
    assert txn.last4 == "1111"
    assert txn.card is None  # labeling belongs to the caller's card registry
    assert txn.txn_date == date(2025, 7, 19)
    assert txn.merchant == "SUPERMERCADO EJEMPLO"
    assert txn.currency == "RD$"
    assert txn.amount_dop == pytest.approx(1089.90)
    assert txn.signed_amount() == pytest.approx(1089.90)


def test_popular_consumo_wrapped_dom():
    # Real Popular emails nest <table><td> without <tr>, flattening the body into
    # the row's first cell. The parser must still find the transaction columns.
    txn = parse_email("m1b", POPULAR, "Notificación de Consumo",
                      _load("popular_consumo_wrapped.html"), usd_to_dop=RATE)
    assert txn is not None
    assert (txn.bank, txn.last4) == (Bank.POPULAR, "1111")
    assert txn.merchant == "SUPERMERCADO EJEMPLO"
    assert txn.amount_dop == pytest.approx(1089.90)
    assert txn.txn_date == date(2025, 7, 19)


def test_popular_consumo_usd_is_converted():
    txn = parse_email("m2", POPULAR, "Notificación de Consumo",
                      _load("popular_consumo_usd.html"), usd_to_dop=RATE)
    assert txn is not None
    assert (txn.bank, txn.last4) == (Bank.POPULAR, "2222")
    assert txn.currency == "US$"
    assert txn.original_amount == pytest.approx(1.99)
    assert txn.amount_dop == pytest.approx(1.99 * RATE)  # 119.40


def test_popular_retiro_maps_to_cash_category():
    txn = parse_email("m3", POPULAR, "Notificación de Retiro",
                      _load("popular_retiro.html"), usd_to_dop=RATE)
    assert txn is not None
    assert txn.tx_type is TxType.RETIRO
    assert txn.txn_date == date(2025, 5, 16)
    assert txn.amount_dop == pytest.approx(600.00)
    txn = categorize(txn, rules={})
    assert txn.category == "Retiro Efectivo"


def test_popular_declined_is_skipped():
    txn = parse_email("m4", POPULAR, "Notificación de Consumo",
                      _load("popular_declinada.html"), usd_to_dop=RATE)
    assert txn is None


def test_qik_purchase():
    txn = parse_email("m5", QIK, "Usaste tu tarjeta de crédito Qik",
                      _load("qik_purchase.html"), usd_to_dop=RATE)
    assert txn is not None
    assert txn.tx_type is TxType.CONSUMO
    assert (txn.bank, txn.last4) == (Bank.QIK, "3333")
    assert txn.txn_date == date(2025, 7, 15)
    assert txn.merchant == "AMAZON 1"
    assert txn.amount_dop == pytest.approx(2031.75)


def test_qik_reversal_is_negative():
    txn = parse_email("m6", QIK, "Se reversó una transacción en tu tarjeta de crédito Qik",
                      _load("qik_reversal.html"), usd_to_dop=RATE)
    assert txn is not None
    assert txn.tx_type is TxType.REVERSAL
    assert (txn.bank, txn.last4) == (Bank.QIK, "3333")
    assert txn.merchant == "Alibaba.com"
    assert txn.amount_dop == pytest.approx(4433.37)
    assert txn.signed_amount() == pytest.approx(-4433.37)


def test_card_label_fallback():
    txn = parse_email("m5", QIK, "Usaste tu tarjeta de crédito Qik",
                      _load("qik_purchase.html"), usd_to_dop=RATE)
    assert txn.card_label == "Qik *3333"  # generic form until a caller labels it


def test_categorize_rules_and_fallback():
    txn = parse_email("m5", QIK, "Usaste tu tarjeta de crédito Qik",
                      _load("qik_purchase.html"), usd_to_dop=RATE)
    # AMAZON has no rule -> falls back to the uncategorized bucket.
    assert categorize(txn, rules={"UBER EATS": "Delivery"}).category == "Otros / sin categoría"
    # A matching rule wins.
    assert categorize(txn, rules={"AMAZON": "Otros / sin categoría"}).category == "Otros / sin categoría"


def test_marketing_email_returns_none():
    assert parse_email("m7", "popularteinforma@popularenlinea.com",
                       "¿Sabías que...?", "<html><body>promo</body></html>",
                       usd_to_dop=RATE) is None


BHD = "Alertas@bhd.com.do"


def test_bhd_outgoing_transfer_is_spend():
    txn = parse_email("b1", BHD, "Transacciones entre productos BHD y a otros Bancos",
                      _load("bhd_transferencia.html"), usd_to_dop=RATE)
    assert txn is not None
    assert txn.tx_type is TxType.CONSUMO
    assert txn.bank is Bank.BHD
    assert txn.last4 == "4444"  # source account, stands in for the card
    assert txn.txn_date == date(2024, 11, 26)
    assert txn.merchant == "BENEFICIARIO PRUEBA, ANA M"  # beneficiary as merchant
    assert txn.currency == "RD$"
    assert txn.amount_dop == pytest.approx(250.00)


def test_bhd_incoming_transfer_is_ignored():
    # Money received (Pago al Instante) has no beneficiary — it's income, not spend.
    txn = parse_email("b2", "Notificaciones@bhd.com.do", "Notificaciones",
                      _load("bhd_recibido.html"), usd_to_dop=RATE)
    assert txn is None


def test_bhd_transfer_to_own_account_is_ignored():
    # Beneficiary is the account holder (greeting name + an extra surname): moving
    # money to your own account is internal movement, not spend.
    txn = parse_email("b5", BHD, "Transacciones entre productos BHD y a otros Bancos",
                      _load("bhd_transferencia_propia.html"), usd_to_dop=RATE)
    assert txn is None


def test_bhd_card_purchase_is_spend():
    # "BHD Notificación de Transacciones": a card purchase in a 6-column table.
    txn = parse_email("b3", BHD, "BHD Notificación de Transacciones",
                      _load("bhd_compra.html"), usd_to_dop=RATE)
    assert txn is not None
    assert txn.bank is Bank.BHD
    assert txn.tx_type is TxType.CONSUMO
    assert txn.last4 == "5555"  # from "Mastercard Mujer Red # 5555"
    assert txn.txn_date == date(2025, 7, 13)
    assert txn.merchant == "TiendaEj #1234567"  # the Comercio column
    assert txn.currency == "RD$"
    assert txn.amount_dop == pytest.approx(5177.00)
    assert txn.signed_amount() == pytest.approx(5177.00)


def test_bhd_card_purchase_reversal_is_negative():
    # A "Reversada" row is a refund: positive amount, REVERSAL type, negative signed.
    txn = parse_email("b4", BHD, "BHD Notificación de Transacciones",
                      _load("bhd_compra_reversada.html"), usd_to_dop=RATE)
    assert txn is not None
    assert txn.tx_type is TxType.REVERSAL
    assert txn.last4 == "5555"
    assert txn.txn_date == date(2025, 7, 7)
    assert txn.merchant == "Consumo BHD"  # reversal row has no Comercio
    assert txn.amount_dop == pytest.approx(4857.00)
    assert txn.signed_amount() == pytest.approx(-4857.00)  # refund self-corrects


BANRESERVAS = "notificaciones@banreservas.com"
BANRESERVAS_APP = "NotificacionesTuBancoApp@banreservas.com"


def test_banreservas_consumo_is_spend():
    txn = parse_email("r1", BANRESERVAS, "Notificaciones Banreservas",
                      _load("banreservas_consumo.html"), usd_to_dop=RATE)
    assert txn is not None
    assert txn.tx_type is TxType.CONSUMO
    assert txn.bank is Bank.BANRESERVAS
    assert txn.last4 == "6666"  # from "VISA CLASICA ••6666"
    assert txn.txn_date == date(2026, 8, 1)  # 01/08/2026 is DD/MM/YYYY, not Jan 8
    assert txn.merchant == "SALON EJEMPLO SANTO DOMINGODO"
    assert txn.currency == "RD$"  # printed "DOP 900.00" — the ISO code is mapped
    assert txn.original_amount == pytest.approx(900.00)
    assert txn.amount_dop == pytest.approx(900.00)
    assert txn.signed_amount() == pytest.approx(900.00)


def test_banreservas_consumo_survives_gmail_forwarding():
    # Gmail prefixes every class name when it forwards, and forwarded mail is
    # what actually reaches us — so the parser must not read classes at all.
    txn = parse_email("r2", BANRESERVAS, "Fwd: Notificaciones Banreservas",
                      _load("banreservas_consumo_forwarded.html"), usd_to_dop=RATE)
    assert txn is not None
    assert (txn.bank, txn.last4) == (Bank.BANRESERVAS, "6666")
    assert txn.merchant == "SALON EJEMPLO SANTO DOMINGODO"
    assert txn.txn_date == date(2026, 8, 1)
    assert txn.amount_dop == pytest.approx(900.00)


def test_banreservas_card_payment_is_ignored():
    # "¡Pago realizado!" — paying your own card off from your own account. The
    # purchases on that card are already logged, so counting the payment too
    # would double-count. Sent from the App address, which shares the domain and
    # so lands in this parser as well.
    txn = parse_email("r3", BANRESERVAS_APP, "Recibo de la transacción",
                      _load("banreservas_pago_tarjeta.html"), usd_to_dop=RATE)
    assert txn is None


def test_banreservas_non_approved_state_is_skipped():
    # Banreservas' wording for a decline or a reversal has never been sampled, so
    # anything that is not APROBADO is skipped rather than guessed at.
    html = _load("banreservas_consumo.html").replace("APROBADO", "DECLINADO")
    txn = parse_email("r4", BANRESERVAS, "Notificaciones Banreservas",
                      html, usd_to_dop=RATE)
    assert txn is None
