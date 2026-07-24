"""One shared Jinja2Templates for the whole app, with i18n injected into every
render. A context processor reads the ``lang`` cookie and exposes ``t`` (the
translator), ``lang``, and localized month-name arrays to every template — so
no route handler has to pass them.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .i18n import MESES, MESES_ABR, normalize_lang, translator

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _i18n(request: Request) -> dict:
    lang = normalize_lang(request.cookies.get("lang"))
    return {
        "lang": lang,
        "t": translator(lang),
        "meses": MESES[lang],
        "meses_abr": MESES_ABR[lang],
    }


templates = Jinja2Templates(directory=str(_TEMPLATE_DIR), context_processors=[_i18n])
