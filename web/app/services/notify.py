"""Notification dispatch and per-user preferences.

Telegram for now; web push arrives with the PWA. The Telegram transport lives
in services.telegram; this module decides *whether* and *what* to send, and
owns the NotificationPref rows the settings page edits.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from budgetcore.messages import sweep_message, transaction_message
from budgetcore.models import Transaction as CoreTxn

from ..models import NotificationPref, User
from ..settings import get_settings
from . import telegram

TELEGRAM = "telegram"


def _as_uuid(user_id):
    return uuid.UUID(user_id) if isinstance(user_id, str) else user_id


def get_pref(session: Session, user_id, channel: str) -> NotificationPref | None:
    return session.scalar(select(NotificationPref).where(
        NotificationPref.user_id == _as_uuid(user_id),
        NotificationPref.channel == channel))


def _ensure_pref(session: Session, user_id, channel: str) -> NotificationPref:
    pref = get_pref(session, user_id, channel)
    if pref is None:
        pref = NotificationPref(user_id=_as_uuid(user_id), channel=channel)
        session.add(pref)
        session.flush()
    return pref


def set_telegram_chat(session: Session, user_id, chat_id: str) -> None:
    """Link (or relink) a Telegram chat and enable alerts on it."""
    pref = _ensure_pref(session, user_id, TELEGRAM)
    pref.telegram_chat_id = str(chat_id)
    pref.enabled = True
    session.commit()


def set_enabled(session: Session, user_id, channel: str, enabled: bool) -> None:
    pref = _ensure_pref(session, user_id, channel)
    pref.enabled = enabled
    session.commit()


def disconnect_telegram(session: Session, user_id) -> None:
    pref = get_pref(session, user_id, TELEGRAM)
    if pref is not None:
        pref.telegram_chat_id = None
        pref.enabled = False
        session.commit()


def transaction_alert(session: Session, user_id, txn: CoreTxn,
                      spent: float, budget: float) -> None:
    pref = get_pref(session, user_id, TELEGRAM)
    if pref is None or not pref.enabled or not pref.telegram_chat_id:
        return
    telegram.send_message(pref.telegram_chat_id,
                          transaction_message(txn, spent, budget))


def notify_sweep(session: Session, calls: int, created: int,
                 elapsed: float) -> None:
    """Tell the owner the category-suggestion sweep just loaded the machine
    running Ollama (the Nitro) — only worth sending when it actually called
    the model. No-op if the owner hasn't linked Telegram."""
    owner = session.scalar(select(User).where(
        func.lower(User.email) == get_settings().default_user_email.lower()))
    if owner is None:
        return
    pref = get_pref(session, owner.id, TELEGRAM)
    if pref is None or not pref.enabled or not pref.telegram_chat_id:
        return
    telegram.send_message(pref.telegram_chat_id,
                          sweep_message(calls, created, elapsed))
