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

from ..auth.deps import current_user, logout_user
from ..auth.service import authenticate
from ..db import get_sessionmaker
from ..models import User
from ..services.account import delete_account, export_data
from ..templating import templates

router = APIRouter()

# Typed by the user to confirm deletion. Accepted in either language.
CONFIRM_WORDS = ("eliminar", "delete")


@router.get("/account")
def account_page(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse(request, "account.html", {})


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
    def refuse(error: str):
        return templates.TemplateResponse(request, "account.html",
                                          {"error": error})

    if confirm.strip().lower() not in CONFIRM_WORDS:
        return refuse("Escribe ELIMINAR para confirmar.")

    with get_sessionmaker()() as session:
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
