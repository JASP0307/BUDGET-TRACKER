"""Parse card-notification emails into normalized Transactions.

Formats are documented in the project's NOTES.md ("Email formats"). Shapes:
  - Banco Popular "Notificación de Consumo"  (card purchase, 5-col table)
  - Banco Popular "Notificación de Retiro"   (ATM withdrawal, 5-col table)
  - Qik "Usaste tu tarjeta..."               (purchase, label/value table)
  - Qik "Se reversó una transacción..."      (reversal, label/value table)
  - BHD "Notificación de Transacciones"      (card purchase, 6-col table)
  - BHD "Transacciones entre productos..."   (transfer, id-tagged fields)
  - Banreservas "Notificación de Consumo"    (card purchase, stacked label/value)

Parsers are pure: raw HTML in, Transaction out. They identify the card only
as (bank, last4) — display labels belong to the caller's card registry.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from bs4 import BeautifulSoup

from .banks import SUPPORTED_BANKS
from .models import Bank, Transaction, TxType

_MONEY_RE = re.compile(r"(RD\$|US\$)\s*([\d.,]+)")
# Popular: "terminada en 1234" — Qik: "que termina en 53*...*1234" (masked PAN).
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


# The card-purchase table's column headers, lowercased, in order.
_BHD_PURCHASE_HEADER = ("fecha", "moneda", "monto", "comercio", "estado", "tipo")
# Card shown as "Mastercard Mujer Red # 1234": 4 digits not followed by more,
# so it never grabs the leading digits of a long "Comercio #1234567".
_BHD_CARD_RE = re.compile(r"#\s*(\d{4})(?!\d)")


def _parse_bhd(
    message_id: str, subject: str, html: str, *, usd_to_dop: float
) -> Transaction | None:
    """BHD alerts (``Alertas@bhd.com.do``) arrive in two shapes, both spend:

    - **Card purchase** — "BHD Notificación de Transacciones", a
      Fecha/Moneda/Monto/Comercio/Estado/Tipo table (``_parse_bhd_purchase``).
    - **Account transfer/payment** — "Transacciones entre productos BHD...",
      id-tagged fields (``_parse_bhd_transfer``).

    Money received and non-transaction mail return None. Both shapes come from
    the same sender, so we parse the HTML once and try the purchase table first
    (structure-based, so it survives Gmail's forwarding rewrites)."""
    soup = BeautifulSoup(html, "html.parser")
    return (_parse_bhd_purchase(message_id, soup, usd_to_dop=usd_to_dop)
            or _parse_bhd_transfer(message_id, soup, usd_to_dop=usd_to_dop))


def _parse_bhd_purchase(
    message_id: str, soup: BeautifulSoup, *, usd_to_dop: float
) -> Transaction | None:
    """Card-purchase alert: one data row under a Fecha/Moneda/Monto/Comercio/
    Estado/Tipo header. Only settled charges count — ``Aprobada`` is spend,
    ``Reversada`` is a refund (negative via ``TxType.REVERSAL``); anything else
    (e.g. a decline) returns None. The amount and currency sit in separate cells
    ("RD" + "$5,177.00"), so we read them apart rather than via ``_money``."""
    rows = [[td.get_text(" ", strip=True) for td in tr.find_all("td")]
            for tr in soup.find_all("tr")]
    rows = [r for r in rows if len(r) == 6]
    header_i = next(
        (i for i, r in enumerate(rows)
         if tuple(c.lower() for c in r) == _BHD_PURCHASE_HEADER),
        None)
    if header_i is None:
        return None  # not a purchase alert — let the transfer parser try
    data = next((r for r in rows[header_i + 1:] if any(r)), None)
    if data is None:
        return None
    fecha, moneda, monto, comercio, estado, _tipo = data

    estado = estado.lower()
    if estado == "aprobada":
        tx_type = TxType.CONSUMO
    elif estado == "reversada":
        tx_type = TxType.REVERSAL
    else:
        return None  # declined / pending — not spend

    amount_m = re.search(r"[\d.,]+", monto)
    fecha_m = _POPULAR_DATE_RE.search(fecha)
    if amount_m is None or fecha_m is None:
        return None
    value = float(amount_m.group(0).replace(",", ""))
    currency = "US$" if moneda.upper().startswith("US") else "RD$"

    card = _BHD_CARD_RE.search(soup.get_text(" ", strip=True))
    return Transaction(
        message_id=message_id,
        bank=Bank.BHD,
        last4=card.group(1) if card else "????",
        tx_type=tx_type,
        txn_date=datetime.strptime(fecha_m.group(1), "%d/%m/%Y").date(),
        merchant=comercio or "Consumo BHD",  # reversals can have no merchant
        amount_dop=_to_dop(currency, value, usd_to_dop),
        original_amount=value,
        currency=currency,
    )


def _name_tokens(name: str) -> set[str]:
    """Uppercase, accent-stripped name words (commas and single letters dropped),
    for order-independent name comparison. 'GÓMEZ, MIGUEL A' -> {GOMEZ, MIGUEL}."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return {t for t in re.sub(r"[^A-Za-z]+", " ", ascii_name).upper().split() if len(t) > 1}


def _same_person(a: str, b: str) -> bool:
    """True when two names denote the same person: the shorter token set (with at
    least two tokens) is contained in the longer. Tolerates a second surname the
    other form omits, or reordering, without matching on a single shared given
    name (Dominican names commonly carry two surnames, inconsistently)."""
    smaller, larger = sorted((_name_tokens(a), _name_tokens(b)), key=len)
    return len(smaller) >= 2 and smaller <= larger


def _bhd_account_holder(soup: BeautifulSoup) -> str | None:
    """The account holder, read from the 'Estimado(a): <NAME>' greeting."""
    for strong in soup.find_all("strong"):
        parent = strong.find_parent()
        if parent is not None and parent.get_text(" ", strip=True).startswith("Estimado"):
            return strong.get_text(" ", strip=True)
    return None


def _parse_bhd_transfer(
    message_id: str, soup: BeautifulSoup, *, usd_to_dop: float
) -> Transaction | None:
    """Transfer/payment alert: id-tagged fields. The beneficiary stands in for
    the merchant, the amount is Monto, and the source account's last 4 digits
    identify the "card". Incoming money (no ``idBeneficiario``) returns None, and
    a transfer to one's *own* account (beneficiary == the account holder) is
    internal movement, not spend, so it returns None too."""
    beneficiary = soup.find(id="idBeneficiario")
    if beneficiary is None:
        return None  # incoming transfer or a non-transaction BHD email
    merchant = beneficiary.get_text(" ", strip=True)
    if not merchant:
        return None

    holder = _bhd_account_holder(soup)
    if holder is not None and _same_person(holder, merchant):
        return None  # moving money to your own account is not a purchase

    amount_cell = soup.find(id="idMonto")
    money = _money(amount_cell.get_text(" ", strip=True)) if amount_cell else None
    if money is None:
        return None
    currency, value = money

    fecha_cell = soup.find(id="idFechayHoraTransaccion")
    fecha = _POPULAR_DATE_RE.search(fecha_cell.get_text(" ", strip=True)
                                    if fecha_cell else "")
    if fecha is None:
        return None

    origin = soup.find(id="idProductoOrigen")
    last4_m = re.search(r"(\d{4})\s*$", origin.get_text(strip=True)) if origin else None

    return Transaction(
        message_id=message_id,
        bank=Bank.BHD,
        last4=last4_m.group(1) if last4_m else "????",
        tx_type=TxType.CONSUMO,
        txn_date=datetime.strptime(fecha.group(1), "%d/%m/%Y").date(),
        merchant=merchant,
        amount_dop=_to_dop(currency, value, usd_to_dop),
        original_amount=value,
        currency=currency,
    )


# Banreservas prints ISO currency codes ("DOP 900.00") where every other bank
# prints a symbol; map them onto the RD$/US$ vocabulary the rest of the pipeline
# speaks (Transaction.currency, _to_dop, and the caller's FX bookkeeping).
_BANRESERVAS_MONEY_RE = re.compile(r"\b(DOP|USD)\s*([\d.,]+)")
_BANRESERVAS_CURRENCY = {"DOP": "RD$", "USD": "US$"}
# "Su tarjeta VISA CLASICA ••6666": two or more mask characters, so it cannot
# match the single-asterisk "Visa *6666" of the payment receipts.
_BANRESERVAS_CARD_RE = re.compile(r"[•·*]{2,}\s*(\d{4})")


def _parse_banreservas(
    message_id: str, subject: str, html: str, *, usd_to_dop: float
) -> Transaction | None:
    """Banreservas "Notificación de Consumo": a card purchase, with the details
    stacked as label-above-value rather than in columns.

    Only card consumptions are spend. The App transaction receipts
    ("¡Pago realizado!", from NotificacionesTuBancoApp@ on the same domain, so
    they land here too) are card payments and transfers between the user's own
    products — logging those would double-count charges already captured from
    this very card. They carry Origen/Destino and no Comercio, and that absence
    is what rejects them."""
    soup = BeautifulSoup(html, "html.parser")
    fields = _banreservas_fields(soup)

    merchant = fields.get("Comercio", "").strip()
    if not merchant:
        return None  # a payment/transfer receipt, or non-transaction mail

    # Strict: only a confirmed approval is spend. Banreservas' wording for a
    # decline or a reversal has never been seen, and guessing it risks logging a
    # decline as a purchase. Mail skipped this way keeps its body for the purge
    # window (see ingest.NOT_LOGGABLE_NOTE), so the first real one is preserved
    # verbatim and this gate can be widened from the actual text.
    if fields.get("Estado", "").strip().upper() != "APROBADO":
        return None

    money = _banreservas_money(fields.get("Monto", ""))
    if money is None:
        return None
    currency, value = money

    # "Fecha de transacción" is the only accented label, so an entity-encoded
    # variant would change the key — fall back to the body, which carries
    # exactly one DD/MM/YYYY date.
    text = soup.get_text(" ", strip=True)
    fecha = (_POPULAR_DATE_RE.search(fields.get("Fecha de transacción", ""))
             or _POPULAR_DATE_RE.search(text))
    if fecha is None:
        return None

    card = _BANRESERVAS_CARD_RE.search(text)
    return Transaction(
        message_id=message_id,
        bank=Bank.BANRESERVAS,
        last4=card.group(1) if card else "????",
        tx_type=TxType.CONSUMO,
        txn_date=datetime.strptime(fecha.group(1), "%d/%m/%Y").date(),
        merchant=merchant,
        amount_dop=_to_dop(currency, value, usd_to_dop),
        original_amount=value,
        currency=currency,
    )


def _banreservas_fields(soup: BeautifulSoup) -> dict[str, str]:
    """Label->value pairs from Banreservas' stacked detail tables.

    Every detail is a two-row inner table — a "Label:" cell above its value cell
    — so in document order each label cell is immediately followed by its value.
    Read that adjacency rather than the CSS classes the layout is built on:
    Gmail prefixes every class when it forwards ("message_title" becomes
    "m_-784…message_title"), and forwarded mail is what we actually receive.

    Any cell ending in a colon is treated as a label, so the result also picks
    up noise — a wrapper cell, or the "…desde tu App Banreservas:" lead-in of a
    receipt. Harmless: callers only ever read the handful of keys they know."""
    cells = [c for c in (td.get_text(" ", strip=True)
                         for td in soup.find_all("td")) if c]
    fields: dict[str, str] = {}
    for label, value in zip(cells, cells[1:]):
        if label.endswith(":"):
            fields.setdefault(label[:-1].strip(), value)
    return fields


def _banreservas_money(text: str) -> tuple[str, float] | None:
    """Return (currency, value) for an ISO-coded amount: "DOP 900.00"."""
    m = _BANRESERVAS_MONEY_RE.search(text)
    if not m:
        return None
    return _BANRESERVAS_CURRENCY[m.group(1)], float(m.group(2).replace(",", ""))


# Which parser handles each supported bank. Adding a bank means a new BankSpec
# in banks.py, a _parse_<bank> above, and one line here.
_PARSERS = {
    Bank.POPULAR: _parse_popular,
    Bank.QIK: _parse_qik,
    Bank.BHD: _parse_bhd,
    Bank.BANRESERVAS: _parse_banreservas,
}
