"""Public privacy and security pages, plus security.txt.

Deliberately public (no `current_user`): someone deciding whether to trust the
app has to be able to read these *before* handing over their email stream.

Both pages are rendered from templates rather than served as static files so
they inherit the i18n context processor and stay bilingual, and so
`LAST_UPDATED` and the contact address live in one place.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..settings import get_settings
from ..templating import templates

router = APIRouter()

# Bump when the substance of either page changes.
LAST_UPDATED = "2026-07-25"
SECURITY_CONTACT = "jabner0703@gmail.com"


def _context() -> dict:
    settings = get_settings()
    return {
        "last_updated": LAST_UPDATED,
        "contact": SECURITY_CONTACT,
        "inbound_domain": settings.inbound_domain,
    }


@router.get("/privacy")
def privacy(request: Request):
    return templates.TemplateResponse(request, "legal/privacy.html", _context())


@router.get("/security")
def security(request: Request):
    return templates.TemplateResponse(request, "legal/security.html", _context())


@router.get("/.well-known/security.txt", response_class=PlainTextResponse)
def security_txt():
    """RFC 9116 contact info for anyone who finds a vulnerability."""
    base = get_settings().base_url
    return (
        f"Contact: mailto:{SECURITY_CONTACT}\n"
        f"Expires: 2027-12-31T23:59:59.000Z\n"
        "Preferred-Languages: es, en\n"
        f"Canonical: {base}/.well-known/security.txt\n"
        f"Policy: {base}/security\n"
        "\n"
        "# Reports are acknowledged within 72 hours. This is a small,\n"
        "# single-operator project: there is no bug bounty, but findings are\n"
        "# taken seriously and credited if you want the credit.\n"
    )
