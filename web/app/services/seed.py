"""Bootstrap for the single-tenant phase: one user, default categories.

Category names must match budgetcore's built-ins ("Retiro Efectivo" for ATM
withdrawals, "Otros / sin categoría" as the fallback) — those two are system
categories every user gets.
"""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Category, InboundAddress, User
from ..settings import get_settings

# Default DR category set (from the developer's own budget).
DEFAULT_CATEGORIES = [
    "Renta", "Combustible", "Suscripciones", "Teléfono", "Diezmo",
    "Salidas", "Delivery", "Mantenimiento",
]
SYSTEM_CATEGORIES = ["Retiro Efectivo", "Otros / sin categoría"]


def bootstrap(session: Session) -> User:
    """Idempotent: create the default user + address + categories if absent.

    The bootstrap account predates password auth; if BUDGET_BOOTSTRAP_PASSWORD
    is set it is (re)applied here so the developer can sign in to the existing
    account and keep its seeded data and inbound routing.
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
        from ..auth.security import hash_password
        user.hashed_password = hash_password(settings.bootstrap_password)
        user.is_verified = True
        session.commit()
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
