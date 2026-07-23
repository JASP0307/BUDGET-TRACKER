"""FastAPI app factory. Single-tenant for Phase 1: every page acts on the
bootstrap user; fastapi-users auth lands in Phase 2."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import Base, get_engine, get_sessionmaker
from .routers import dashboard, webhook
from .services.seed import bootstrap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Alembic takes over once the schema stabilizes; create_all is enough
    # while the app is single-tenant and pre-beta.
    Base.metadata.create_all(get_engine())
    with get_sessionmaker()() as session:
        bootstrap(session)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Budget Tracker", lifespan=_lifespan)
    app.include_router(webhook.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
