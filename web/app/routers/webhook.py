"""Inbound mail webhooks — Postmark and Resend — plus the Telegram bot.

Both mail routes end in the same `ingest.handle_inbound`; only the delivery
mechanics differ, and both are mounted so inbound can be moved one provider at a
time. Store-first: whatever happens during processing, the raw email is archived
and 200 is returned so the provider does not retry forever.

The Postmark URL carries a shared secret (Postmark can't sign requests) and
production allowlists its IPs at the proxy. Resend signs, so that route verifies
the signature *and* keeps a path secret.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request

from ..db import get_sessionmaker
from ..services import inbound_resend, notify, svix, telegram
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


@router.post("/webhooks/resend-inbound/{secret}")
async def resend_inbound(secret: str, request: Request) -> dict:
    settings = get_settings()
    # No signing secret configured means the Resend route is not in use. Refuse
    # rather than fall back to the path secret alone: an unsigned inbound
    # endpoint lets anyone who guesses a forward address inject transactions.
    if not settings.resend_webhook_secret:
        raise HTTPException(status_code=404)
    if not hmac.compare_digest(secret, settings.webhook_secret):
        raise HTTPException(status_code=404)

    body = await request.body()
    if not svix.verify(settings.resend_webhook_secret,
                       request.headers.get("svix-id", ""),
                       request.headers.get("svix-timestamp", ""),
                       request.headers.get("svix-signature", ""),
                       body):
        raise HTTPException(status_code=401, detail="bad signature")

    event = await request.json()
    if event.get("type") != "email.received":
        return {"status": "ignored"}  # delivery/bounce events share the endpoint

    # The body and headers live behind a second call. If it fails, answer 5xx so
    # Resend redelivers — writing a row now would make handle_inbound's
    # idempotency return that empty row forever and lose the email silently.
    try:
        payload = inbound_resend.payload_from_event(event)
    except inbound_resend.ReceivedEmailUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

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
