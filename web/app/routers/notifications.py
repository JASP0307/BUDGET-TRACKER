"""Notification settings: link a Telegram chat, toggle alerts, and configure
per-category email thresholds.

Telegram linking uses a deep link (t.me/<bot>?start=<token>) whose token is
signed for the current user; the Telegram webhook (routers.webhook) redeems
it. Email needs no linking step (the account's own address is already known),
just the master toggle plus optional per-category overrides — a category with
no override uses notify.DEFAULT_ALERT_THRESHOLD_PCT. Web push will slot in
here as a further channel later.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from ..templating import templates

from ..auth.deps import current_user
from ..db import get_sessionmaker
from ..models import Category, User
from ..services import notify, telegram

router = APIRouter()


@router.get("/notifications")
def notifications_page(request: Request, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        pref = notify.get_pref(session, user.id, notify.TELEGRAM)
        linked = pref is not None and bool(pref.telegram_chat_id)
        enabled = bool(pref and pref.enabled)

        email_pref = notify.get_pref(session, user.id, notify.EMAIL)
        email_enabled = bool(email_pref and email_pref.enabled)
        categories = session.scalars(select(Category)
                                     .where(Category.user_id == user.id)
                                     .order_by(Category.sort_order)).all()
        category_rows = []
        for cat in categories:
            alert = notify.get_category_alert(session, user.id, cat.id)
            category_rows.append({
                "category": cat,
                "threshold": alert.threshold_pct if alert else notify.DEFAULT_ALERT_THRESHOLD_PCT,
                "cat_enabled": alert.enabled if alert else True,
            })
    bot_username = telegram.get_bot_username()
    link = None
    if bot_username and not linked:
        link = telegram.deep_link(telegram.make_link_token(user.id))
    return templates.TemplateResponse(request, "notifications.html", {
        "bot_configured": bot_username is not None,
        "linked": linked,
        "enabled": enabled,
        "telegram_link": link,
        "email_enabled": email_enabled,
        "category_rows": category_rows,
    })


@router.post("/notifications/telegram/toggle")
def toggle_telegram(enabled: str = Form(""), user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        notify.set_enabled(session, user.id, notify.TELEGRAM,
                           enabled == "on")
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/telegram/disconnect")
def disconnect_telegram(user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        notify.disconnect_telegram(session, user.id)
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/telegram/test")
def test_telegram(user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        pref = notify.get_pref(session, user.id, notify.TELEGRAM)
        chat_id = pref.telegram_chat_id if pref else None
    if chat_id:
        telegram.send_message(chat_id, "🔔 Mensaje de prueba de Cualto.")
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/email/toggle")
def toggle_email(enabled: str = Form(""), user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        notify.set_enabled(session, user.id, notify.EMAIL, enabled == "on")
    return RedirectResponse("/notifications", status_code=303)


@router.post("/notifications/email/thresholds")
async def save_email_thresholds(request: Request, user: User = Depends(current_user)):
    form = await request.form()
    with get_sessionmaker()() as session:
        categories = {c.id for c in session.scalars(select(Category)
                     .where(Category.user_id == user.id))}
        for key, value in form.items():
            if not key.startswith("threshold_"):
                continue
            try:
                cat_id = uuid.UUID(key.removeprefix("threshold_"))
                threshold = max(0.0, min(1000.0, float(value or 100)))
            except ValueError:
                continue
            if cat_id not in categories:
                continue
            cat_enabled = form.get(f"enabled_{cat_id}") == "on"
            notify.set_category_alert(session, user.id, cat_id, threshold, cat_enabled)
        session.commit()
    return RedirectResponse("/notifications", status_code=303)
