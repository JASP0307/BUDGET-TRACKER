"""Request-scoped auth: the session cookie holds a user id and the password
generation it was issued under; `current_user` turns that into a User and
rejects anonymous, stale, or superseded sessions.

`NotAuthenticated` is translated to a redirect to /login by an exception
handler registered in main.py, so page handlers can simply depend on a user.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from ..db import get_sessionmaker
from ..models import User
from ..settings import get_settings

_SESSION_KEY = "user_id"
_VERSION_KEY = "session_version"


class NotAuthenticated(Exception):
    """No valid logged-in user for a request that requires one."""


def is_admin(user: User) -> bool:
    """Single-owner admin for now: the account whose email matches
    BUDGET_USER_EMAIL. Real roles arrive with an is_admin column once Alembic
    is in place."""
    return user.email.lower() == get_settings().default_user_email.lower()


def login_user(request: Request, user: User) -> None:
    request.session[_SESSION_KEY] = str(user.id)
    request.session[_VERSION_KEY] = user.session_version


def logout_user(request: Request) -> None:
    request.session.pop(_SESSION_KEY, None)
    request.session.pop(_VERSION_KEY, None)


def _load_session_user(request: Request) -> User | None:
    raw = request.session.get(_SESSION_KEY)
    if not raw:
        return None
    try:
        user_id = uuid.UUID(raw)
    except (ValueError, TypeError):
        return None
    with get_sessionmaker()() as session:
        user = session.scalar(select(User).where(User.id == user_id))
        if user is None:
            return None
        # A password change bumps session_version, so cookies minted before it
        # stop working — that is how a user evicts someone else from their
        # account. Sessions predating this field carry no version and are stale.
        if request.session.get(_VERSION_KEY) != user.session_version:
            return None
        return user


def current_user(request: Request) -> User:
    """Dependency for pages that require a verified, logged-in user."""
    user = _load_session_user(request)
    if user is None or not user.is_verified:
        raise NotAuthenticated()
    request.state.user = user  # read by base.html for the header
    request.state.is_admin = is_admin(user)  # read by base.html for the nav
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """Dependency for admin-only pages. Non-admins get a 404 so the area's
    existence isn't advertised."""
    if not is_admin(user):
        raise HTTPException(status_code=404)
    return user


def optional_user(request: Request) -> User | None:
    """Like current_user but returns None instead of raising — for pages
    (login, register) that render differently when already signed in."""
    user = _load_session_user(request)
    if user is not None and user.is_verified:
        request.state.user = user
    return user
