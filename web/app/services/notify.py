"""Notification dispatch and per-user preferences.

Telegram is real-time, per transaction; email is deliberately not — it only
fires when a category's spend crosses its alert threshold (default 100%, i.e.
budget exceeded; customizable per category via CategoryAlertPref), so a user
who wants email doesn't get one per purchase. Web push arrives with the PWA.
The transports live in services.telegram / services.email; this module decides
*whether* and *what* to send, and owns the NotificationPref /
CategoryAlertPref rows the settings page edits.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from budgetcore.messages import sweep_message, transaction_message
from budgetcore.models import Transaction as CoreTxn

from ..models import CategoryAlertPref, NotificationPref, User
from ..settings import get_settings
from . import email, telegram

TELEGRAM = "telegram"
EMAIL = "email"
DEFAULT_ALERT_THRESHOLD_PCT = 100.0


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


def get_category_alert(session: Session, user_id,
                       category_id) -> CategoryAlertPref | None:
    return session.scalar(select(CategoryAlertPref).where(
        CategoryAlertPref.user_id == _as_uuid(user_id),
        CategoryAlertPref.category_id == category_id))


def set_category_alert(session: Session, user_id, category_id,
                       threshold_pct: float, enabled: bool) -> None:
    row = get_category_alert(session, user_id, category_id)
    if row is None:
        row = CategoryAlertPref(user_id=_as_uuid(user_id), category_id=category_id)
        session.add(row)
    row.threshold_pct = threshold_pct
    row.enabled = enabled


def transaction_alert(session: Session, user_id, txn: CoreTxn,
                      spent: float, budget: float,
                      category_id=None, category_name: str | None = None) -> None:
    pref = get_pref(session, user_id, TELEGRAM)
    if pref is not None and pref.enabled and pref.telegram_chat_id:
        telegram.send_message(pref.telegram_chat_id,
                              transaction_message(txn, spent, budget))
    _email_threshold_alert(session, user_id, txn, spent, budget,
                           category_id, category_name)


def _email_threshold_alert(session: Session, user_id, txn: CoreTxn,
                           spent: float, budget: float,
                           category_id, category_name: str | None) -> None:
    """Fire an email only on the transaction that *crosses* the category's
    threshold — not on every transaction while already over it, which is
    exactly the per-purchase noise email is meant to avoid (Telegram already
    covers that). A reversal that drops spend back under the threshold lets a
    later purchase cross it again; nothing is persisted to prevent that, the
    crossing is recomputed fresh from the running total each time.
    """
    if budget <= 0 or category_id is None:
        return
    pref = get_pref(session, user_id, EMAIL)
    if pref is None or not pref.enabled:
        return
    cat_pref = get_category_alert(session, user_id, category_id)
    if cat_pref is not None and not cat_pref.enabled:
        return
    threshold = cat_pref.threshold_pct if cat_pref else DEFAULT_ALERT_THRESHOLD_PCT
    pct_after = spent / budget * 100
    pct_before = (spent - txn.signed_amount()) / budget * 100
    if pct_before >= threshold or pct_after < threshold:
        return
    user = session.get(User, _as_uuid(user_id))
    if user is None:
        return
    email.send_budget_alert_email(user.email, category_name or "", spent, budget)


def notify_sweep(session: Session, calls: int, created: int,
                 elapsed: float) -> None:
    """Tell the owner the category-suggestion sweep found something to review.
    Caller only invokes this when the sweep actually created suggestions.
    No-op if the owner hasn't linked Telegram."""
    owner = session.scalar(select(User).where(
        func.lower(User.email) == get_settings().default_user_email.lower()))
    if owner is None:
        return
    pref = get_pref(session, owner.id, TELEGRAM)
    if pref is None or not pref.enabled or not pref.telegram_chat_id:
        return
    telegram.send_message(pref.telegram_chat_id,
                          sweep_message(calls, created, elapsed))
