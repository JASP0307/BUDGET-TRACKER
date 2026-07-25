"""Outbound transactional email (verification, later password reset).

Mirrors how the rest of the app treats optional integrations: if Postmark is
configured it sends; otherwise it logs the message so local development and
tests work without any mail provider.
"""

from __future__ import annotations

import logging

import requests

from ..settings import get_settings

log = logging.getLogger("email")

_POSTMARK_URL = "https://api.postmarkapp.com/email"


def send_email(to: str, subject: str, text_body: str) -> None:
    settings = get_settings()
    if not settings.postmark_token:
        log.info("email (not sent, no Postmark token) to=%s subject=%s\n%s",
                 to, subject, text_body)
        return
    resp = requests.post(
        _POSTMARK_URL,
        headers={"X-Postmark-Server-Token": settings.postmark_token,
                 "Accept": "application/json"},
        json={"From": settings.from_email, "To": to,
              "Subject": subject, "TextBody": text_body,
              "MessageStream": "outbound"},
        timeout=15,
    )
    resp.raise_for_status()


def send_verification_email(to: str, verify_url: str) -> None:
    send_email(
        to,
        "Confirma tu correo — Cualto",
        "¡Bienvenido a Cualto!\n\n"
        "Confirma tu correo para activar tu cuenta:\n\n"
        f"{verify_url}\n\n"
        "El enlace vence en 24 horas. Si no creaste esta cuenta, ignora "
        "este mensaje.",
    )


def send_password_reset_email(to: str, reset_url: str) -> None:
    send_email(
        to,
        "Restablece tu contraseña — Cualto",
        "Recibimos una solicitud para restablecer tu contraseña.\n\n"
        "Crea una nueva desde este enlace:\n\n"
        f"{reset_url}\n\n"
        "El enlace vence en 1 hora. Si no lo solicitaste, ignora este mensaje; "
        "tu contraseña seguirá igual.",
    )
