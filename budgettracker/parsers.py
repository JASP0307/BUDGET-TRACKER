"""Parse card-notification emails into normalized Transactions.

Formats are documented in ../../NOTES.md ("Email formats"). Four shapes:
  - Banco Popular "Notificación de Consumo"  (card purchase, 5-col table)
  - Banco Popular "Notificación de Retiro"   (ATM withdrawal, 5-col table)
  - Qik "Usaste tu tarjeta..."               (purchase, label/value table)
  - Qik "Se reversó una transacción..."      (reversal, label/value table)
"""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from .models import Transaction, TxType

# Banco Popular identifies the card only by its last 4 digits in the greeting.
_POPULAR_CARDS = {
    "1111": "Popular VISA ISI *1111",
    "2222": "Popular Visa Débito Clásica *2222",
}

_MONEY_RE = re.compile(r"(RD\$|US\$)\s*([\d.,]+)")
_LAST4_RE = re.compile(r"terminada en\s+(\d{4})")
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


def parse_email(
    message_id: str, sender: str, subject: str, html: str, *, usd_to_dop: float
) -> Transaction | None:
    """Dispatch to the right parser. Returns None for non-transaction mail."""
    sender = sender.lower()
    if "popularenlinea.com" in sender:
        return _parse_popular(message_id, subject, html, usd_to_dop=usd_to_dop)
    if "qik.do" in sender:
        return _parse_qik(message_id, subject, html, usd_to_dop=usd_to_dop)
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

    last4_match = _LAST4_RE.search(text)
    last4 = last4_match.group(1) if last4_match else "????"
    card = _POPULAR_CARDS.get(last4, f"Popular *{last4}")

    return Transaction(
        message_id=message_id,
        card=card,
        tx_type=tx_type,
        txn_date=datetime.strptime(fecha_str, "%d/%m/%Y").date(),
        merchant=merchant.strip(),
        amount_dop=_to_dop(currency, value, usd_to_dop),
        original_amount=value,
        currency=currency,
    )


def _popular_data_row(html: str) -> tuple[str, str, str, str, str] | None:
    """Return the 5 cells of the transaction row: amount, moneda, fecha,
    comercio/cajero, estatus. The data row is the <tr> whose first cell is a
    money amount (the header row uses <th>)."""
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) >= 5 and _MONEY_RE.search(cells[0]):
            return cells[0], cells[1], cells[2], cells[3], cells[4]
    return None


def _parse_qik(
    message_id: str, subject: str, html: str, *, usd_to_dop: float
) -> Transaction | None:
    tx_type = TxType.REVERSAL if "revers" in subject.lower() else TxType.CONSUMO

    fields = _qik_label_values(html)

    amount_src = fields.get("Monto", "")
    money = _money(amount_src)
    if money is None:
        # Fall back to the intro sentence ("...de RD$ 2,031.75 en AMAZON...").
        money = _money(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    if money is None:
        return None
    currency, value = money

    merchant = fields.get("Localidad") or fields.get("Lugar") or "Qik (sin detalle)"

    fecha = fields.get("Fecha y hora")
    txn_date = _parse_qik_date(fecha) if fecha else date.today()

    return Transaction(
        message_id=message_id,
        card="Qik *3333",
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
