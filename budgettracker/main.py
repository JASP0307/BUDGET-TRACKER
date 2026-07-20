"""Orchestrator — the cron entry point.

Flow (see NOTES.md "Proposed pipeline"):
  1. Auth once for Gmail (read) + Sheets (write).
  2. Fetch recent notification emails.
  3. Skip ones already logged (message_id present in the Sheet).
  4. Parse -> categorize -> append row -> Telegram alert with remaining budget.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from . import config as config_mod
from . import gmail_client, notifier, sheets
from .categorize import categorize
from .parsers import parse_email

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


def run(config_path: str = "config.toml") -> int:
    cfg = config_mod.load(config_path)
    creds = _credentials(cfg.gmail_credentials_file, cfg.gmail_token_file)
    gmail = build("gmail", "v1", credentials=creds)
    sheet = sheets.get_service(creds)

    processed = sheets.read_processed_ids(sheet, cfg.spreadsheet_id, cfg.transactions_tab)
    emails = gmail_client.fetch_candidates(gmail, cfg.fetch_window)

    logged = 0
    for email in emails:
        if email.message_id in processed:
            continue
        txn = parse_email(email.message_id, email.sender, email.subject,
                          email.html, usd_to_dop=cfg.usd_to_dop)
        if txn is None:
            continue
        txn = categorize(txn, cfg.rules)

        sheets.append_transaction(sheet, cfg.spreadsheet_id, cfg.transactions_tab, txn)
        spent = sheets.month_to_date_spend(
            sheet, cfg.spreadsheet_id, cfg.transactions_tab, txn.category)
        budget = cfg.budget.get(txn.category or "", 0.0)
        notifier.send(cfg.telegram_bot_token, cfg.telegram_chat_id,
                      notifier.transaction_message(txn, spent, budget))
        logged += 1

    print(f"Processed {len(emails)} email(s), logged {logged} new transaction(s).")
    return logged


if __name__ == "__main__":
    run()
