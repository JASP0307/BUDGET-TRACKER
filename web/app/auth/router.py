"""Auth pages: register, login, logout, email verification, password reset.

Server-rendered forms (no JS needed). Registration seeds a full account and
emails a signed verification link; login is gated on a verified address.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..db import get_sessionmaker
from ..models import User
from ..services.email import send_password_reset_email, send_verification_email
from ..settings import get_settings
from . import service
from .deps import login_user, logout_user, optional_user
from .security import make_reset_token, make_verify_token, read_verify_token

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


def _send_verification(user: User) -> None:
    url = f"{get_settings().base_url}/verify?token={make_verify_token(user.id)}"
    send_verification_email(user.email, url)


# ---- register ----

@router.get("/register")
def register_form(request: Request, user=Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "auth/register.html", {})


@router.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip()
    error = None
    if not _valid_email(email):
        error = "Ingresa un correo válido."
    elif len(password) < MIN_PASSWORD:
        error = f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres."
    if error:
        return templates.TemplateResponse(
            request, "auth/register.html", {"error": error, "email": email})

    with get_sessionmaker()() as session:
        try:
            user = service.register_user(session, email, password)
        except service.EmailTaken:
            return templates.TemplateResponse(
                request, "auth/register.html",
                {"error": "Ya existe una cuenta con ese correo.", "email": email})
        _send_verification(user)

    return templates.TemplateResponse(request, "auth/notice.html", {
        "title": "Revisa tu correo",
        "message": "Te enviamos un enlace para confirmar tu cuenta. "
                   "Ábrelo para activarla; el enlace vence en 24 horas.",
    })


# ---- verify ----

@router.get("/verify")
def verify(request: Request, token: str = ""):
    user_id = read_verify_token(token) if token else None
    if user_id is None:
        return templates.TemplateResponse(request, "auth/notice.html", {
            "title": "Enlace inválido o vencido",
            "message": "Solicita un nuevo enlace de confirmación desde el "
                       "inicio de sesión.",
            "link_url": "/login", "link_text": "Ir a iniciar sesión",
        })
    with get_sessionmaker()() as session:
        service.mark_verified(session, user_id)
    return templates.TemplateResponse(request, "auth/notice.html", {
        "title": "Correo confirmado",
        "message": "Tu cuenta está activa. Ya puedes iniciar sesión.",
        "link_url": "/login", "link_text": "Iniciar sesión",
    })


# ---- login / logout ----

@router.get("/login")
def login_form(request: Request, user=Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "auth/login.html", {})


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    with get_sessionmaker()() as session:
        user = service.authenticate(session, email, password)
        if user is None:
            return templates.TemplateResponse(
                request, "auth/login.html",
                {"error": "Correo o contraseña incorrectos.", "email": email})
        if not user.is_verified:
            _send_verification(user)
            return templates.TemplateResponse(
                request, "auth/login.html",
                {"error": "Confirma tu correo antes de entrar. Te reenviamos "
                          "el enlace.", "email": email})
        login_user(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


# ---- password reset ----

_CHECK_EMAIL_NOTICE = {
    "title": "Revisa tu correo",
    "message": "Si existe una cuenta con ese correo, te enviamos un enlace para "
               "restablecer tu contraseña. El enlace vence en 1 hora.",
}


@router.get("/forgot")
def forgot_form(request: Request, user=Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "auth/forgot.html", {})


@router.post("/forgot")
def forgot(request: Request, email: str = Form(...)):
    # Always show the same notice so the page never reveals which emails exist.
    with get_sessionmaker()() as session:
        user = service.user_by_email(session, email)
        if user is not None and user.hashed_password:
            token = make_reset_token(user.id, user.hashed_password)
            url = f"{get_settings().base_url}/reset?token={token}"
            send_password_reset_email(user.email, url)
    return templates.TemplateResponse(request, "auth/notice.html",
                                      _CHECK_EMAIL_NOTICE)


def _invalid_reset(request: Request):
    return templates.TemplateResponse(request, "auth/notice.html", {
        "title": "Enlace inválido o vencido",
        "message": "Solicita un nuevo enlace para restablecer tu contraseña.",
        "link_url": "/forgot", "link_text": "Restablecer contraseña",
    })


@router.get("/reset")
def reset_form(request: Request, token: str = ""):
    with get_sessionmaker()() as session:
        if service.resolve_reset_token(session, token) is None:
            return _invalid_reset(request)
    return templates.TemplateResponse(request, "auth/reset.html", {"token": token})


@router.post("/reset")
def reset(request: Request, token: str = Form(...), password: str = Form(...)):
    with get_sessionmaker()() as session:
        user = service.resolve_reset_token(session, token)
        if user is None:
            return _invalid_reset(request)
        if len(password) < MIN_PASSWORD:
            return templates.TemplateResponse(request, "auth/reset.html", {
                "token": token,
                "error": f"La contraseña debe tener al menos {MIN_PASSWORD} "
                         "caracteres."})
        service.set_password(session, user, password)
    return templates.TemplateResponse(request, "auth/notice.html", {
        "title": "Contraseña actualizada",
        "message": "Ya puedes iniciar sesión con tu nueva contraseña.",
        "link_url": "/login", "link_text": "Iniciar sesión",
    })
