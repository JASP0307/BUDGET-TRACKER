"""Postmark inbound webhook.

The URL carries a shared secret (Postmark can't sign requests); production
additionally allowlists Postmark's IPs at the proxy. Store-first: whatever
happens during processing, the raw email is archived and 200 is returned so
Postmark does not retry forever.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request

from ..db import get_sessionmaker
from ..services import notify, telegram
from ..services.ingest import handle_inbound
from ..settings import get_settings

router = APIRouter()


@router.post("/webhooks/postmark-inbound/{secret}")
async def postmark_inbound(secret: str, request: Request) -> dict:
    if not hmac.compare_digest(secret, get_settings().webhook_secret):
        raise HTTPException(status_code=404)
    payload = await request.json()
    with get_sessionmaker()() as session:
        raw = handle_inbound(session, payload)
        return {"status": raw.processing_status}


@router.post("/webhooks/telegram/{secret}")
async def telegram_inbound(secret: str, request: Request) -> dict:
    """Receives bot updates. A `/start <token>` from the account-link deep link
    binds that chat to the user the token was signed for."""
    if not hmac.compare_digest(secret, get_settings().telegram_webhook_secret):
        raise HTTPException(status_code=404)
    update = await request.json()
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    chat_id = (message.get("chat") or {}).get("id")

    if chat_id is not None and text.startswith("/start"):
        parts = text.split(maxsplit=1)
        user_id = telegram.read_link_token(parts[1]) if len(parts) > 1 else None
        if user_id is not None:
            with get_sessionmaker()() as session:
                notify.set_telegram_chat(session, user_id, str(chat_id))
            telegram.send_message(
                str(chat_id),
                "✅ ¡Conectado! Te avisaré aquí sobre tu presupuesto.")
    return {"ok": True}
