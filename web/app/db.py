"""Engine/session plumbing. The URL comes from settings so tests can point
at an in-memory SQLite while production uses Postgres."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        # SQLite needs this flag to be shared across FastAPI's threadpool.
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
        _engine = create_engine(url, **kwargs)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


def reset_engine() -> None:
    """Testing hook: forget the cached engine so a new URL takes effect."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
