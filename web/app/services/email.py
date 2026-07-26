"""Outbound transactional email (verification, password reset).

Sends through **Resend**. Postmark was the original provider and still handles
inbound, but it gates outbound behind a manual account approval that left real
signups unconfirmable for a day; Resend requires only a verified domain, and its
free tier covers this scale. Inbound is a separate migration.

Mirrors how the rest of the app treats optional integrations: if a key is
configured it sends; otherwise it logs the message so local development and
tests work without any mail provider.

Sending never raises. A provider outage must not turn a successful signup into
a 500 *after* the account row is committed — the caller has no way to undo that,
and the user would be left with an account they can't reach. Instead the whole
message is logged, which keeps the link recoverable by an operator. Production
is not allowed to run without a provider at all: settings.require_production_secrets
blocks startup, so the no-key path below is a development convenience only.
"""

from __future__ import annotations

import logging

import requests

from ..settings import get_settings

log = logging.getLogger("email")

_RESEND_URL = "https://api.resend.com/emails"


def send_email(to: str, subject: str, text_body: str) -> None:
    settings = get_settings()
    if not settings.resend_token:
        log.info("email (not sent, no Resend key) to=%s subject=%s\n%s",
                 to, subject, text_body)
        return
    try:
        resp = requests.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_token}",
                     "Accept": "application/json"},
            json={"from": settings.from_email, "to": [to],
                  "subject": subject, "text": text_body},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        # The reason is in the response body, not the status — an unverified
        # sending domain, a rejected From address and a rate limit all surface
        # as a 4xx. Without the body the log says only "422" and the operator
        # has to reproduce the call by hand to learn anything, which is exactly
        # what happened on the first live failure.
        detail = getattr(exc.response, "text", "") or str(exc)
        log.error("email (send failed: %s) to=%s subject=%s\n%s",
                  detail, to, subject, text_body)


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
