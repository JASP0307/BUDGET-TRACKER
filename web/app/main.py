"""FastAPI app factory. Phase 2: session-cookie auth (see app.auth); every
page acts on the logged-in user. The single-tenant bootstrap user still
exists so the dev's inbound routing and seeded data survive the transition."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth.deps import NotAuthenticated
from .auth.router import router as auth_router
from .db import Base, get_engine, get_sessionmaker
from .routers import (account, admin, dashboard, guide, legal,
                      notifications, setup, webhook)
from .services.seed import bootstrap
from .settings import get_settings, require_production_secrets
from .templating import STATIC_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Sessions expire after two weeks rather than living as long as the browser
# keeps the cookie.
SESSION_MAX_AGE = 14 * 24 * 60 * 60


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Before anything touches the database: refuse to serve a production
    # deployment whose secrets are missing or still the published dev defaults.
    require_production_secrets()
    # Alembic takes over once the schema stabilizes; create_all is enough
    # while the schema is still moving and the deployment is pre-beta.
    Base.metadata.create_all(get_engine())
    with get_sessionmaker()() as session:
        bootstrap(session)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Cualto", lifespan=_lifespan)
    # https_only marks the cookie Secure — required in production so the session
    # can never ride an accidental plaintext request; off in dev, where there is
    # no TLS. same_site="lax" is what blocks cross-site form POSTs.
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       same_site="lax", https_only=settings.is_production,
                       max_age=SESSION_MAX_AGE)

    @app.exception_handler(NotAuthenticated)
    async def _redirect_to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=303)

    # Everything served from here is public and self-hosted on purpose: the
    # onboarding recordings must not need a third-party player, which would
    # want a frame-src hole in the CSP this deployment is still owed.
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(webhook.router)
    app.include_router(auth_router)
    app.include_router(legal.router)
    app.include_router(guide.router)
    app.include_router(setup.router)
    app.include_router(notifications.router)
    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
