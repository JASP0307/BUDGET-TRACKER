"""Request-scoped auth: the session cookie holds a user id; `current_user`
turns it into a User and rejects anonymous or stale sessions.

`NotAuthenticated` is translated to a redirect to /login by an exception
handler registered in main.py, so page handlers can simply depend on a user.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import select

from ..db import get_sessionmaker
from ..models import User

_SESSION_KEY = "user_id"


class NotAuthenticated(Exception):
    """No valid logged-in user for a request that requires one."""


def login_user(request: Request, user: User) -> None:
    request.session[_SESSION_KEY] = str(user.id)


def logout_user(request: Request) -> None:
    request.session.pop(_SESSION_KEY, None)


def _load_session_user(request: Request) -> User | None:
    raw = request.session.get(_SESSION_KEY)
    if not raw:
        return None
    try:
        user_id = uuid.UUID(raw)
    except (ValueError, TypeError):
        return None
    with get_sessionmaker()() as session:
        return session.scalar(select(User).where(User.id == user_id))


def current_user(request: Request) -> User:
    """Dependency for pages that require a verified, logged-in user."""
    user = _load_session_user(request)
    if user is None or not user.is_verified:
        raise NotAuthenticated()
    request.state.user = user  # read by base.html for the header
    return user


def optional_user(request: Request) -> User | None:
    """Like current_user but returns None instead of raising — for pages
    (login, register) that render differently when already signed in."""
    user = _load_session_user(request)
    if user is not None and user.is_verified:
        request.state.user = user
    return user
