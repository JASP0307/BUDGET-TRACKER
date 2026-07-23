"""Tests for the daily heartbeat: row counting and message text."""

from datetime import date

from budgettracker.notifier import heartbeat_message
from budgettracker.sheets import _SHEETS_EPOCH, _count_rows

TODAY = date(2026, 7, 22)  # a Wednesday; week starts Mon 2026-07-20


def _row(d):
    """A minimal A2:I row whose date cell (column B) is `d` as an ISO string."""
    return ["msg-id", d.isoformat(), "Qik *3333", "CONSUMO", "MERCHANT",
            "Comida", 500.0, 500.0, "DOP"]


def _serial(d):
    return (d - _SHEETS_EPOCH).days


def test_counts_today_and_week():
    rows = [
        _row(date(2026, 7, 22)),  # today
        _row(date(2026, 7, 22)),  # today
        _row(date(2026, 7, 20)),  # Monday — in week
        _row(date(2026, 7, 19)),  # Sunday — previous week
        _row(date(2026, 7, 1)),   # earlier in month
    ]
    assert _count_rows(rows, TODAY) == (2, 3)


def test_serial_dates_count_like_iso_dates():
    # Sheets returns column B as a serial number on unformatted reads.
    row = _row(TODAY)
    row[1] = _serial(TODAY)
    assert _count_rows([row], TODAY) == (1, 1)


def test_malformed_and_short_rows_are_ignored():
    rows = [["msg-id"], _row(TODAY)]
    rows.append(_row(TODAY))
    rows[-1][1] = "not-a-date"
    assert _count_rows(rows, TODAY) == (1, 1)


def test_message_singular_and_plural():
    single = heartbeat_message(1, 5, TODAY)
    assert "1</b> transacción" in single
    plural = heartbeat_message(3, 12, TODAY)
    assert "3</b> transacciones" in plural
    assert "22 jul" in plural
    assert "Tracker vivo" in plural
    assert "12" in plural
