"""Bootstrap for the single-tenant phase: one user, default categories.

Category names must match budgetcore's built-ins ("Retiro Efectivo" for ATM
withdrawals, "Otros / sin categoría" as the fallback) — those two are system
categories every user gets.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, InboundAddress, User
from ..settings import get_settings

log = logging.getLogger("seed")

# Default DR category set (from the developer's own budget).
DEFAULT_CATEGORIES = [
    "Renta", "Combustible", "Suscripciones", "Teléfono", "Diezmo",
    "Salidas", "Delivery", "Mantenimiento",
]
SYSTEM_CATEGORIES = ["Retiro Efectivo", "Otros / sin categoría"]


def bootstrap(session: Session) -> User:
    """Idempotent: create the default user + address + categories if absent.

    BUDGET_BOOTSTRAP_PASSWORD seeds the owner's password, but **only when the
    account has none** — either because this call just created it, or because it
    predates password auth, which is what the variable originally existed for.

    It is deliberately not re-applied on every start. Doing so silently reverted
    any password set through the UI on the next restart or deploy. The
    `session_version` bump from that change did survive — existing sessions
    stayed evicted — but the old credential came back, so anyone who knew it
    (it sits in plaintext in the environment) could simply log in again, which
    makes the eviction pointless. Once the account has a password, the variable
    is ignored and should be unset.
    """
    settings = get_settings()
    email = settings.default_user_email
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, is_verified=True)
        session.add(user)
        session.flush()
        session.add(InboundAddress(user_id=user.id, token=new_token()))
        seed_categories(session, user.id)
        session.commit()
    if settings.bootstrap_password:
        if not user.hashed_password:
            from ..auth.security import hash_password
            user.hashed_password = hash_password(settings.bootstrap_password)
            user.is_verified = True
            session.commit()
        else:
            log.warning(
                "BUDGET_BOOTSTRAP_PASSWORD is set but %s already has a password; "
                "ignoring it. Unset the variable — it is keeping a plaintext "
                "password in this process's environment for no benefit.", email)
    return user


def seed_categories(session: Session, user_id) -> None:
    order = 0
    for name in DEFAULT_CATEGORIES:
        session.add(Category(user_id=user_id, name=name, sort_order=order))
        order += 1
    for name in SYSTEM_CATEGORIES:
        session.add(Category(user_id=user_id, name=name, sort_order=order,
                             is_system=True))
        order += 1


def new_token() -> str:
    return secrets.token_hex(6)  # 12 lowercase hex chars
