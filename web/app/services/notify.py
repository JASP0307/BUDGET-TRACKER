"""Notification dispatch. Telegram for now; web push arrives with the PWA."""

from __future__ import annotations

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from budgetcore.messages import transaction_message
from budgetcore.models import Transaction as CoreTxn

from ..models import NotificationPref
from ..settings import get_settings

_API = "https://api.telegram.org/bot{token}/sendMessage"


def transaction_alert(session: Session, user_id, txn: CoreTxn,
                      spent: float, budget: float) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    pref = session.scalar(select(NotificationPref).where(
        NotificationPref.user_id == user_id,
        NotificationPref.channel == "telegram",
        NotificationPref.enabled.is_(True)))
    if pref is None or not pref.telegram_chat_id:
        return
    resp = requests.post(
        _API.format(token=settings.telegram_bot_token),
        json={"chat_id": pref.telegram_chat_id,
              "text": transaction_message(txn, spent, budget),
              "parse_mode": "HTML"},
        timeout=15,
    )
    resp.raise_for_status()
