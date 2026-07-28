"""The public setup guide at /help.

Deliberately public (no `current_user`), like the legal pages: the whole of
onboarding happens inside Gmail, so the walkthrough has to be linkable to
someone who has not signed up yet — a WhatsApp message, a reply to "how does
this even work", the eventual landing page.

It duplicates the /setup wording on purpose. /setup is the wizard, tied to one
account's live state and its own intake address; this is the explanation,
readable by anyone, and the place the full recording lives.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from budgetcore.banks import bank_choices

from ..templating import templates

router = APIRouter()


@router.get("/help")
def help_page(request: Request):
    return templates.TemplateResponse(request, "help.html", {
        "banks": [display for _, display in bank_choices()],
    })
