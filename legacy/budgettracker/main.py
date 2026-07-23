"""Orchestrator — the cron entry point.

Flow (see NOTES.md "Proposed pipeline"):
  1. Auth once for Gmail (read) + Sheets (write).
  2. Fetch recent notification emails.
  3. Skip ones already logged (message_id present in the Sheet).
  4. Parse -> categorize -> append row -> Telegram alert with remaining budget.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from budgetcore.categorize import categorize
from budgetcore.parsers import parse_email

from . import config as config_mod
from . import gmail_client, notifier, sheets
from .cards import resolve_card

# Combined scopes: read mail, read/write the budget sheet.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _credentials(credentials_file: str, token_file: str) -> Credentials:
    creds = None
    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(token_file).write_text(creds.to_json(), encoding="utf-8")
    return creds


def run(config_path: str = "config.toml", *, dry_run: bool = False,
        backfill: bool = False) -> int:
    cfg = config_mod.load(config_path)
    creds = _credentials(cfg.gmail_credentials_file, cfg.gmail_token_file)
    gmail = build("gmail", "v1", credentials=creds)

    # Backfill widens the window to reach the 1st of the current month and stays
    # silent on Telegram; normal runs use the configured (short) window.
    today = date.today()
    window = f"{today.day + 1}d" if backfill else cfg.fetch_window
    emails = gmail_client.fetch_candidates(gmail, window)

    if dry_run:
        return _dry_run(cfg, emails)

    sheet = sheets.get_service(creds)
    processed = sheets.read_processed_ids(sheet, cfg.spreadsheet_id, cfg.transactions_tab)
    signatures = sheets.read_signatures(sheet, cfg.spreadsheet_id, cfg.transactions_tab)

    logged = 0
    skipped_dupes = 0
    for email in emails:
        if email.message_id in processed:
            continue
        txn = parse_email(email.message_id, email.sender, email.subject,
                          email.html, usd_to_dop=cfg.usd_to_dop)
        if txn is None:
            continue
        # Backfill only fills the current month; older emails in the wide window
        # are ignored so we don't log partial prior months.
        if backfill and (txn.txn_date.year != today.year
                         or txn.txn_date.month != today.month):
            continue
        txn = categorize(resolve_card(txn), cfg.rules)

        # Guard against duplicate bank notifications (same content, different id).
        sig = sheets.signature(txn.card, txn.txn_date, txn.merchant, txn.signed_amount())
        if sig in signatures:
            skipped_dupes += 1
            continue
        signatures.add(sig)

        sheets.append_transaction(sheet, cfg.spreadsheet_id, cfg.transactions_tab, txn)
        if not backfill:
            spent = sheets.month_to_date_spend(
                sheet, cfg.spreadsheet_id, cfg.transactions_tab, txn.category)
            budget = cfg.budget.get(txn.category or "", 0.0)
            notifier.send(cfg.telegram_bot_token, cfg.telegram_chat_id,
                          notifier.transaction_message(txn, spent, budget))
        logged += 1

    mode = "backfilled" if backfill else "logged"
    dupe_note = f", skipped {skipped_dupes} duplicate(s)" if skipped_dupes else ""
    print(f"Processed {len(emails)} email(s), {mode} {logged} new transaction(s)"
          f"{dupe_note}.")
    return logged


def heartbeat(config_path: str = "config.toml", *, dry_run: bool = False) -> None:
    """Daily proof-of-life message: one Sheets read, no Gmail fetch."""
    cfg = config_mod.load(config_path)
    creds = _credentials(cfg.gmail_credentials_file, cfg.gmail_token_file)
    sheet = sheets.get_service(creds)
    today = date.today()
    today_count, week_count = sheets.count_today_and_week(
        sheet, cfg.spreadsheet_id, cfg.transactions_tab, today)
    text = notifier.heartbeat_message(today_count, week_count, today)
    if dry_run:
        print(text)
        print("(dry run — not sent)")
        return
    notifier.send(cfg.telegram_bot_token, cfg.telegram_chat_id, text)
    print(f"Heartbeat sent: {today_count} txn(s) today, {week_count} this week.")


def _dry_run(cfg, emails) -> int:
    """Parse and print without touching the Sheet or Telegram."""
    parsed = 0
    print(f"Fetched {len(emails)} candidate email(s):\n")
    for email in emails:
        txn = parse_email(email.message_id, email.sender, email.subject,
                          email.html, usd_to_dop=cfg.usd_to_dop)
        if txn is None:
            print(f"  · skipped (not a transaction): {email.subject!r}")
            continue
        txn = categorize(resolve_card(txn), cfg.rules)
        parsed += 1
        print(f"  ✓ {txn.txn_date} | {txn.category:<22} | "
              f"RD${txn.signed_amount():>10,.2f} | {txn.merchant} ({txn.card})")
    print(f"\nParsed {parsed} transaction(s). (dry run — nothing written)")
    return parsed


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Budget tracker poller")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--dry-run", action="store_true",
                   help="fetch + parse + print only; no Sheet writes or Telegram")
    p.add_argument("--backfill", action="store_true",
                   help="log this month's past transactions to the Sheet; no Telegram")
    p.add_argument("--heartbeat", action="store_true",
                   help="send the daily proof-of-life Telegram message; skips Gmail")
    args = p.parse_args()
    if args.heartbeat:
        heartbeat(args.config, dry_run=args.dry_run)
    else:
        run(args.config, dry_run=args.dry_run, backfill=args.backfill)
