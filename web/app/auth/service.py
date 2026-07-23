"""User lifecycle: register, authenticate, verify, reset password.

A new user is created together with the things every user needs to receive
mail and see a dashboard: an inbound address and the default category set —
the same helpers the single-tenant bootstrap uses.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import InboundAddress, User
from ..services.seed import new_token, seed_categories
from .security import (hash_password, password_fingerprint, read_reset_token,
                       verify_password)

# A real Argon2 hash to verify against when the email is unknown, so a failed
# login costs the same whether or not the account exists (no timing oracle).
_DUMMY_HASH = hash_password("timing-equalizer")


class EmailTaken(Exception):
    """Raised when registering an email that already exists."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def register_user(session: Session, email: str, password: str) -> User:
    """Create an unverified user with an inbound address and seeded categories.

    Caller is responsible for validating email/password shape first.
    """
    email = normalize_email(email)
    existing = session.scalar(
        select(User).where(func.lower(User.email) == email))
    if existing is not None:
        raise EmailTaken(email)

    user = User(email=email, hashed_password=hash_password(password),
                is_verified=False)
    session.add(user)
    session.flush()
    session.add(InboundAddress(user_id=user.id, token=new_token()))
    seed_categories(session, user.id)
    session.commit()
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    """Return the user when the password matches, else None. Runs the hash
    verify even for unknown emails so timing doesn't reveal which exist."""
    user = session.scalar(
        select(User).where(func.lower(User.email) == normalize_email(email)))
    reference = user.hashed_password if user and user.hashed_password else _DUMMY_HASH
    ok = verify_password(password, reference)
    return user if (ok and user is not None) else None


def mark_verified(session: Session, user_id) -> User | None:
    if isinstance(user_id, str):
        try:
            user_id = uuid.UUID(user_id)
        except ValueError:
            return None
    user = session.get(User, user_id)
    if user is None:
        return None
    if not user.is_verified:
        user.is_verified = True
        session.commit()
    return user


def user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(
        select(User).where(func.lower(User.email) == normalize_email(email)))


def resolve_reset_token(session: Session, token: str) -> User | None:
    """Return the user a reset token is valid for, or None. Valid means: well
    signed, unexpired, and the embedded fingerprint still matches the user's
    current password hash (so a used or superseded link is rejected)."""
    parsed = read_reset_token(token)
    if parsed is None:
        return None
    raw_id, fingerprint = parsed
    try:
        user_id = uuid.UUID(raw_id)
    except (ValueError, TypeError):
        return None
    user = session.get(User, user_id)
    if user is None or not user.hashed_password:
        return None
    if password_fingerprint(user.hashed_password) != fingerprint:
        return None
    return user


def set_password(session: Session, user: User, new_password: str) -> None:
    user.hashed_password = hash_password(new_password)
    session.commit()
