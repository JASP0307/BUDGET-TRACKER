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
