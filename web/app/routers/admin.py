"""Owner-only admin: the inbound-email debug queue and FX rates.

The queue surfaces RawEmails that didn't become transactions — `failed`
(parser raised: usually a new bank-email template) and `unrecognized` (no
inbound address matched) — so a new format is caught early. Each row opens a
detail view with the decrypted body, the reparse/debug archive in action.
FX rates replace the legacy global usd_to_dop and are updated here.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import crypto
from ..auth.deps import require_admin
from ..db import get_sessionmaker
from ..models import FxRate, RawEmail, User

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# The statuses worth a human's attention, and the filters offered in the UI.
PROBLEM = ("failed", "unrecognized")
FILTERS = {
    "problem": ("Problemas", PROBLEM),
    "failed": ("Fallidos", ("failed",)),
    "unrecognized": ("No reconocidos", ("unrecognized",)),
    "skipped": ("Omitidos", ("skipped",)),
    "all": ("Todos", None),
}


def _counts(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(RawEmail.processing_status, func.count())
        .group_by(RawEmail.processing_status)).all()
    return {status: count for status, count in rows}


@router.get("")
def overview(request: Request, status: str = "problem",
             admin: User = Depends(require_admin)):
    if status not in FILTERS:
        status = "problem"
    _, wanted = FILTERS[status]
    with get_sessionmaker()() as session:
        query = select(RawEmail).order_by(RawEmail.received_at.desc()).limit(100)
        if wanted is not None:
            query = query.where(RawEmail.processing_status.in_(wanted))
        emails = session.scalars(query).all()
        owners = dict(session.execute(
            select(User.id, User.email).where(
                User.id.in_([e.user_id for e in emails if e.user_id]))).all())
        rates = session.scalars(select(FxRate)
                                .order_by(FxRate.effective_date.desc())
                                .limit(20)).all()
        counts = _counts(session)
        return templates.TemplateResponse(request, "admin/overview.html", {
            "counts": counts,
            "problem_total": sum(counts.get(s, 0) for s in PROBLEM),
            "emails": emails,
            "owners": owners,
            "filters": FILTERS,
            "active_filter": status,
            "rates": rates,
            "today": date.today().isoformat(),
        })


@router.get("/emails/{email_id}")
def email_detail(request: Request, email_id: uuid.UUID,
                 admin: User = Depends(require_admin)):
    with get_sessionmaker()() as session:
        raw = session.get(RawEmail, email_id)
        if raw is None:
            return RedirectResponse("/admin", status_code=303)
        owner = session.get(User, raw.user_id) if raw.user_id else None
        try:
            body = crypto.decrypt(raw.html_body)
        except Exception as exc:  # noqa: BLE001 — never 500 the debug view
            body = f"[no se pudo descifrar el cuerpo: {exc}]"
        return templates.TemplateResponse(request, "admin/email.html", {
            "raw": raw,
            "owner_email": owner.email if owner else None,
            "body": body,
        })


@router.post("/emails/{email_id}/delete")
def delete_email(email_id: uuid.UUID, admin: User = Depends(require_admin)):
    with get_sessionmaker()() as session:
        raw = session.get(RawEmail, email_id)
        if raw is not None:
            session.delete(raw)
            session.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/fx")
def add_fx_rate(rate: float = Form(...), effective_date: str = Form(...),
                admin: User = Depends(require_admin)):
    try:
        when = date.fromisoformat(effective_date)
    except ValueError:
        return RedirectResponse("/admin", status_code=303)
    if rate <= 0:
        return RedirectResponse("/admin", status_code=303)
    with get_sessionmaker()() as session:
        existing = session.scalar(select(FxRate).where(
            FxRate.pair == "USD/DOP", FxRate.effective_date == when))
        if existing is not None:
            existing.rate = rate
        else:
            session.add(FxRate(pair="USD/DOP", rate=rate, effective_date=when))
        session.commit()
    return RedirectResponse("/admin", status_code=303)
