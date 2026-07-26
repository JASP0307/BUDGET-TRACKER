"""Outbound transactional email (verification, later password reset).

Mirrors how the rest of the app treats optional integrations: if Postmark is
configured it sends; otherwise it logs the message so local development and
tests work without any mail provider.

Sending never raises. A provider outage must not turn a successful signup into
a 500 *after* the account row is committed — the caller has no way to undo that,
and the user would be left with an account they can't reach. Instead the whole
message is logged, which keeps the link recoverable by an operator. Production
is not allowed to run without a provider at all: settings.require_production_secrets
blocks startup, so the no-token path below is a development convenience only.
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
    try:
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
    except requests.RequestException as exc:
        log.error("email (send failed: %s) to=%s subject=%s\n%s",
                  exc, to, subject, text_body)


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
