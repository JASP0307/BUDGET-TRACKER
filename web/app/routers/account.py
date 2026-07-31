"""Account settings: download your data, delete your account.

Deletion is irreversible, so it is gated on re-entering the password and on
typing the confirmation word — the two-step pattern users already expect from
destructive actions. Export is a plain JSON download; no job queue, because a
personal budget is small enough to serialize in the request.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from sqlalchemy import select

from ..auth.deps import current_user, logout_user
from ..auth.service import authenticate, normalize_email
from ..db import get_sessionmaker
from ..models import User, VerifiedForwarder
from ..services.account import delete_account, export_data
from ..templating import templates

router = APIRouter()

# Typed by the user to confirm deletion. Accepted in either language.
CONFIRM_WORDS = ("eliminar", "delete")


def _page_context(session, user: User, **extra) -> dict:
    forwarders = session.scalars(
        select(VerifiedForwarder)
        .where(VerifiedForwarder.user_id == user.id)
        .order_by(VerifiedForwarder.address)).all()
    return {"forwarders": forwarders, "user_email": user.email, **extra}


@router.get("/account")
def account_page(request: Request, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        return templates.TemplateResponse(request, "account.html",
                                          _page_context(session, user))


@router.post("/account/forwarders/add")
def add_forwarder(request: Request, address: str = Form(""),
                  user: User = Depends(current_user)):
    """Trust another mailbox to forward bank mail into this account.

    Adding one is already an authenticated action on your own account, so there
    is no separate confirmation round-trip — the thing being defended against is
    a *stranger* forwarding into an account they don't own, not the owner adding
    their own second Gmail.
    """
    addr = normalize_email(address)
    with get_sessionmaker()() as session:
        def render(error=None, notice=None):
            return templates.TemplateResponse(
                request, "account.html",
                _page_context(session, user, error=error, notice=notice))

        if "@" not in addr:
            return render(error="Escribe un correo válido.")
        if addr == normalize_email(user.email):
            return render(notice="Ese ya es el correo de tu cuenta.")
        exists = session.scalar(select(VerifiedForwarder).where(
            VerifiedForwarder.user_id == user.id,
            VerifiedForwarder.address == addr))
        if exists is None:
            session.add(VerifiedForwarder(user_id=user.id, address=addr))
            session.commit()
        return render(notice=f"Ahora aceptamos reenvíos desde {addr}.")


@router.post("/account/forwarders/remove")
def remove_forwarder(request: Request, address: str = Form(""),
                     user: User = Depends(current_user)):
    addr = normalize_email(address)
    with get_sessionmaker()() as session:
        row = session.scalar(select(VerifiedForwarder).where(
            VerifiedForwarder.user_id == user.id,
            VerifiedForwarder.address == addr))
        if row is not None:
            session.delete(row)
            session.commit()
        return templates.TemplateResponse(
            request, "account.html",
            _page_context(session, user,
                          notice=f"Ya no aceptamos reenvíos desde {addr}."))


@router.get("/account/export")
def export(user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        data = export_data(session, session.get(User, user.id))
    body = json.dumps(data, indent=2, ensure_ascii=False)
    return Response(
        body, media_type="application/json",
        headers={"Content-Disposition":
                 'attachment; filename="budget-tracker-datos.json"'})


@router.post("/account/delete")
def delete(request: Request, password: str = Form(""), confirm: str = Form(""),
           user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        def refuse(error: str):
            return templates.TemplateResponse(
                request, "account.html",
                _page_context(session, user, error=error))

        if confirm.strip().lower() not in CONFIRM_WORDS:
            return refuse("Escribe ELIMINAR para confirmar.")

        # Re-authenticate: a session cookie alone must not be enough to destroy
        # an account.
        confirmed = authenticate(session, user.email, password)
        if confirmed is None or confirmed.id != user.id:
            return refuse("Contraseña incorrecta.")
        delete_account(session, confirmed)

    logout_user(request)
    return templates.TemplateResponse(request, "auth/notice.html", {
        "title": "Cuenta eliminada",
        "message": "Borramos tu cuenta y todos tus datos. Recuerda quitar el "
                   "filtro de reenvío en Gmail para que tu banco deje de "
                   "enviarnos correos.",
        "link_url": "/login", "link_text": "Volver al inicio",
    })
