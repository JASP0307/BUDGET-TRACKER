"""Notification settings: link a Telegram chat and toggle alerts.

Linking uses a deep link (t.me/<bot>?start=<token>) whose token is signed for
the current user; the Telegram webhook (routers.webhook) redeems it. Web push
will slot in here as a second channel later.
"""

from __future__ import annotations


from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from ..templating import templates

from ..auth.deps import current_user
from ..db import get_sessionmaker
from ..models import User
from ..services import notify, telegram

router = APIRouter()


@router.get("/notifications")
def notifications_page(request: Request, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        pref = notify.get_pref(session, user.id, notify.TELEGRAM)
        linked = pref is not None and bool(pref.telegram_chat_id)
        enabled = bool(pref and pref.enabled)
    bot_username = telegram.get_bot_username()
    link = None
    if bot_username and not linked:
        link = telegram.deep_link(telegram.make_link_token(user.id))
    return templates.TemplateResponse(request, "notifications.html", {
        "bot_configured": bot_username is not None,
        "linked": linked,
        "enabled": enabled,
        "telegram_link": link,
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
