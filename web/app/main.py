"""FastAPI app factory. Phase 2: session-cookie auth (see app.auth); every
page acts on the logged-in user. The single-tenant bootstrap user still
exists so the dev's inbound routing and seeded data survive the transition."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from .auth.deps import NotAuthenticated
from .auth.router import router as auth_router
from .db import Base, get_engine, get_sessionmaker
from .routers import dashboard, setup, webhook
from .services.seed import bootstrap
from .settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Alembic takes over once the schema stabilizes; create_all is enough
    # while the schema is still moving and the deployment is pre-beta.
    Base.metadata.create_all(get_engine())
    with get_sessionmaker()() as session:
        bootstrap(session)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Budget Tracker", lifespan=_lifespan)
    app.add_middleware(SessionMiddleware, secret_key=get_settings().session_secret,
                       same_site="lax", https_only=False)

    @app.exception_handler(NotAuthenticated)
    async def _redirect_to_login(request: Request, exc: NotAuthenticated):
        return RedirectResponse("/login", status_code=303)

    app.include_router(webhook.router)
    app.include_router(auth_router)
    app.include_router(setup.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
