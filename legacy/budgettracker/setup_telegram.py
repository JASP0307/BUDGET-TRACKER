"""One-time helper: connect the Telegram bot and save its creds to config.

Run and follow the prompts:  python -m budgettracker.setup_telegram
Prompts for the BotFather token (kept local — never printed), discovers your
chat id from getUpdates, writes both into config.toml, and sends a test message.
"""

from __future__ import annotations

import re
import sys
from getpass import getpass
from pathlib import Path

import requests


def _discover_chat_id(token: str) -> str | None:
    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
    resp.raise_for_status()
    updates = resp.json().get("result", [])
    for update in reversed(updates):
        chat = (update.get("message") or update.get("edited_message") or {}).get("chat")
        if chat and "id" in chat:
            return str(chat["id"])
    return None


def _patch_config(path: Path, token: str, chat_id: str) -> None:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'bot_token = ".*"', f'bot_token = "{token}"', text)
    text = re.sub(r'chat_id = ".*"', f'chat_id = "{chat_id}"', text)
    path.write_text(text, encoding="utf-8")


def main(config_path: str = "config.toml") -> int:
    token = getpass("Paste your BotFather token (input hidden): ").strip()
    if not token:
        print("No token entered.")
        return 1

    chat_id = _discover_chat_id(token)
    if not chat_id:
        print("\nNo messages found for this bot yet.")
        print("Open Telegram, send your bot any message (e.g. 'hola'), then "
              "re-run this command.")
        return 1

    _patch_config(Path(config_path), token, chat_id)

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "✅ Budget Tracker conectado."},
        timeout=15,
    )
    resp.raise_for_status()
    print(f"Connected. chat_id={chat_id} saved to {config_path}. "
          "Check Telegram for the test message.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
