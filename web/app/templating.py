"""One shared Jinja2Templates for the whole app, with i18n injected into every
render. A context processor reads the ``lang`` cookie and exposes ``t`` (the
translator), ``lang``, and localized month-name arrays to every template — so
no route handler has to pass them.

It also exposes ``clip()``, which resolves an optional walkthrough recording to
a URL only when the file is actually on disk.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .i18n import MESES, MESES_ABR, normalize_lang, translator

_TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
CLIP_DIR = STATIC_DIR / "clips"


def _i18n(request: Request) -> dict:
    lang = normalize_lang(request.cookies.get("lang"))
    return {
        "lang": lang,
        "t": translator(lang),
        "meses": MESES[lang],
        "meses_abr": MESES_ABR[lang],
    }


def clip(name: str) -> dict | None:
    """The URLs for an onboarding screen recording, or None when it isn't there.

    The recordings are deliberately not in git — the repo is public and video is
    permanent weight — so a fresh clone, a self-hoster, and the app before the
    clips are filmed all have to render fine without them. Every slot asks for
    its clip and simply renders nothing when the answer is None, which keeps
    "no media yet" off the list of things that can break a page.

    Returns ``{"src", "poster"}``; ``poster`` is None if no .jpg sits beside the
    video.
    """
    if "/" in name or "\\" in name or name.startswith("."):
        return None  # never let a template compose a path out of the static dir
    video = CLIP_DIR / f"{name}.mp4"
    if not video.is_file():
        return None
    poster = CLIP_DIR / f"{name}.jpg"
    return {
        "src": f"/static/clips/{name}.mp4",
        "poster": f"/static/clips/{name}.jpg" if poster.is_file() else None,
    }


templates = Jinja2Templates(directory=str(_TEMPLATE_DIR), context_processors=[_i18n])
templates.env.globals["clip"] = clip
