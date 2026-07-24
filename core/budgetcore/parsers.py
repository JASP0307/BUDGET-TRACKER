"""Parse card-notification emails into normalized Transactions.

Formats are documented in the project's NOTES.md ("Email formats"). Four shapes:
  - Banco Popular "Notificación de Consumo"  (card purchase, 5-col table)
  - Banco Popular "Notificación de Retiro"   (ATM withdrawal, 5-col table)
  - Qik "Usaste tu tarjeta..."               (purchase, label/value table)
  - Qik "Se reversó una transacción..."      (reversal, label/value table)

Parsers are pure: raw HTML in, Transaction out. They identify the card only
as (bank, last4) — display labels belong to the caller's card registry.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from .banks import SUPPORTED_BANKS
from .models import Bank, Transaction, TxType

_MONEY_RE = re.compile(r"(RD\$|US\$)\s*([\d.,]+)")
# Popular: "terminada en 1111" — Qik: "que termina en 53*...*3333" (masked PAN).
_LAST4_RE = re.compile(r"termina(?:da)?\s+en\s+[\d*]*(\d{4})")
_POPULAR_DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


def _money(text: str) -> tuple[str, float] | None:
    """Return (currency, value) for the first money token in `text`."""
    m = _MONEY_RE.search(text)
    if not m:
        return None
    currency = m.group(1)
    value = float(m.group(2).replace(",", ""))
    return currency, value


def _to_dop(currency: str, value: float, usd_to_dop: float) -> float:
    return value if currency == "RD$" else round(value * usd_to_dop, 2)


def _last4(text: str) -> str:
    m = _LAST4_RE.search(text)
    return m.group(1) if m else "????"


def parse_email(
    message_id: str, sender: str, subject: str, html: str, *, usd_to_dop: float
) -> Transaction | None:
    """Dispatch to the right parser by matching the sender against the bank
    registry. Returns None for mail from no known bank (or a bank with no
    parser wired in ``_PARSERS`` yet)."""
    sender = sender.lower()
    for spec in SUPPORTED_BANKS:
        if spec.domain in sender:
            parser = _PARSERS.get(spec.bank)
            if parser is None:
                return None
            return parser(message_id, subject, html, usd_to_dop=usd_to_dop)
    return None


def _parse_popular(
    message_id: str, subject: str, html: str, *, usd_to_dop: float
) -> Transaction | None:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    # Declined charges are notifications too — never log them as spend.
    if "declinada" in text.lower():
        return None

    if "Retiro" in subject:
        tx_type = TxType.RETIRO
    elif "Consumo" in subject:
        tx_type = TxType.CONSUMO
    else:
        return None

    row = _popular_data_row(html)
    if row is None:
        return None
    amount_str, _moneda, fecha_str, merchant, estatus = row

    if not estatus.lower().startswith("aprobada"):
        return None

    money = _money(amount_str)
    if money is None:
        return None
    currency, value = money

    return Transaction(
        message_id=message_id,
        bank=Bank.POPULAR,
        last4=_last4(text),
        tx_type=tx_type,
        txn_date=datetime.strptime(fecha_str, "%d/%m/%Y").date(),
        merchant=merchant.strip(),
        amount_dop=_to_dop(currency, value, usd_to_dop),
        original_amount=value,
        currency=currency,
    )


def _popular_data_row(html: str) -> tuple[str, str, str, str, str] | None:
    """Return the 5 transaction cells: amount, moneda, fecha, comercio/cajero,
    estatus.

    Popular's real emails wrap everything in nested `<table><td>` blocks with no
    `<tr>`, so BeautifulSoup can flatten the whole body into the row's first
    cell. Rather than assume column 0, find the cell that *is* a money amount and
    read the five columns starting there."""
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        for i, cell in enumerate(cells):
            if _MONEY_RE.fullmatch(cell) and i + 5 <= len(cells):
                return tuple(cells[i:i + 5])  # type: ignore[return-value]
    return None


def _parse_qik(
    message_id: str, subject: str, html: str, *, usd_to_dop: float
) -> Transaction | None:
    tx_type = TxType.REVERSAL if "revers" in subject.lower() else TxType.CONSUMO

    fields = _qik_label_values(html)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    amount_src = fields.get("Monto", "")
    money = _money(amount_src)
    if money is None:
        # Fall back to the intro sentence ("...de RD$ 2,031.75 en AMAZON...").
        money = _money(text)
    if money is None:
        return None
    currency, value = money

    merchant = fields.get("Localidad") or fields.get("Lugar") or "Qik (sin detalle)"

    fecha = fields.get("Fecha y hora")
    txn_date = _parse_qik_date(fecha) if fecha else date.today()

    return Transaction(
        message_id=message_id,
        bank=Bank.QIK,
        last4=_last4(text),
        tx_type=tx_type,
        txn_date=txn_date,
        merchant=merchant.strip(),
        amount_dop=_to_dop(currency, value, usd_to_dop),
        original_amount=value,
        currency=currency,
    )


def _qik_label_values(html: str) -> dict[str, str]:
    """Collect label->value pairs from Qik's two-column detail rows."""
    soup = BeautifulSoup(html, "html.parser")
    known = {"Localidad", "Lugar", "Fecha y hora", "Monto", "Balance Disponible", "Estatus"}
    out: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) == 2 and cells[0] in known and cells[0] not in out:
            out[cells[0]] = cells[1]
    return out


def _parse_qik_date(raw: str) -> date:
    """'07-15-2026 08:54 PM (AST)' -> date(2026, 7, 15). US-style MM-DD-YYYY."""
    cleaned = re.sub(r"\s*\(AST\)\s*", "", raw).strip()
    return datetime.strptime(cleaned, "%m-%d-%Y %I:%M %p").date()


# Which parser handles each supported bank. Adding a bank means a new BankSpec
# in banks.py, a _parse_<bank> above, and one line here.
_PARSERS = {
    Bank.POPULAR: _parse_popular,
    Bank.QIK: _parse_qik,
}
