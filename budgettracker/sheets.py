"""Google Sheets: append transactions, read budgets, compute month-to-date.

The transactions tab is the source of truth for dedupe: its `message_id`
column tells us which emails were already logged (gmail.readonly can't label).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .models import Transaction

# gmail.readonly for mail + read/write for the sheet.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TX_HEADER = ["message_id", "date", "card", "type", "merchant",
             "category", "amount_dop", "original", "currency"]


def get_service(creds: Credentials):
    return build("sheets", "v4", credentials=creds)


def read_processed_ids(service, spreadsheet_id: str, tab: str) -> set[str]:
    """Return the set of message_ids already logged (first column)."""
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{tab}!A2:A").execute()
    return {row[0] for row in resp.get("values", []) if row}


def signature(card: str, txn_date, merchant: str, signed_amount: float) -> tuple:
    """Content key used to catch duplicate bank notifications (which arrive with
    different message ids). A reversal has the opposite sign, so it never
    collides with the original charge."""
    return (card, txn_date.isoformat(), merchant, round(float(signed_amount), 2))


def read_signatures(service, spreadsheet_id: str, tab: str) -> set[tuple]:
    """Content signatures of all rows already in the sheet."""
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{tab}!A2:I",
        valueRenderOption="UNFORMATTED_VALUE").execute()
    sigs: set[tuple] = set()
    for row in resp.get("values", []):
        if len(row) < 7:
            continue
        d = _cell_date(row[1])
        if d is None:
            continue
        try:
            amount = round(float(row[6]), 2)
        except (ValueError, TypeError):
            continue
        sigs.add((str(row[2]), d.isoformat(), str(row[4]), amount))
    return sigs


def append_transaction(service, spreadsheet_id: str, tab: str, txn: Transaction) -> None:
    row = [
        txn.message_id,
        txn.txn_date.isoformat(),
        txn.card,
        txn.tx_type.value,
        txn.merchant,
        txn.category or "",
        txn.signed_amount(),
        txn.original_amount,
        txn.currency,
    ]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{tab}!A:I",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


# Google Sheets serial-date epoch.
_SHEETS_EPOCH = date(1899, 12, 30)


def _cell_date(value) -> date | None:
    """Column B may come back as a Sheets serial (number) or an ISO string."""
    if isinstance(value, (int, float)):
        return _SHEETS_EPOCH + timedelta(days=int(value))
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def count_today_and_week(service, spreadsheet_id: str, tab: str,
                         today: date | None = None) -> tuple[int, int]:
    """Count logged transactions for today and for the current week (Mon–today).

    One unformatted read serves both counts; used by the daily heartbeat."""
    today = today or date.today()
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{tab}!A2:I",
        valueRenderOption="UNFORMATTED_VALUE").execute()
    return _count_rows(resp.get("values", []), today)


def _count_rows(rows, today: date) -> tuple[int, int]:
    week_start = today - timedelta(days=today.weekday())
    today_count = week_count = 0
    for row in rows:
        if len(row) < 2:
            continue
        d = _cell_date(row[1])
        if d is None:
            continue
        if d == today:
            today_count += 1
        if week_start <= d <= today:
            week_count += 1
    return today_count, week_count


def month_to_date_spend(service, spreadsheet_id: str, tab: str, category: str,
                        today: date | None = None) -> float:
    """Sum signed amounts for `category` within the current month.

    Reads unformatted values so dates arrive as serials regardless of the
    sheet's display format (a formatted read is locale-dependent)."""
    today = today or date.today()
    resp = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"{tab}!A2:I",
        valueRenderOption="UNFORMATTED_VALUE").execute()
    total = 0.0
    for row in resp.get("values", []):
        # columns: 0 id, 1 date, 5 category, 6 amount
        if len(row) < 7 or row[5] != category:
            continue
        d = _cell_date(row[1])
        if d and d.year == today.year and d.month == today.month:
            try:
                total += float(row[6])
            except (ValueError, TypeError):
                continue
    return total
