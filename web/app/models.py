"""ORM schema — the plan's §3 data model.

Types are kept portable (String/JSON/Uuid) so the same models run on the
production Postgres and the SQLite used for dev/tests. Multi-user columns
exist from day one even while the app runs single-tenant.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey,
                        Integer, String, Text, UniqueConstraint, Uuid)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    hashed_password: Mapped[str | None] = mapped_column(String(200))  # Phase 2
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Bumped on every password change; session cookies carry the value they were
    # minted with, so changing the password logs out every other device.
    # See auth.deps.login_user / _load_session_user.
    session_version: Mapped[int] = mapped_column(Integer, default=0,
                                                 server_default="0")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Santo_Domingo")
    locale: Mapped[str] = mapped_column(String(16), default="es-DO")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    cards: Mapped[list["Card"]] = relationship(back_populates="user")


class InboundAddress(Base):
    __tablename__ = "inbound_addresses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token: Mapped[str] = mapped_column(String(32), unique=True)  # u_<token> local part
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("user_id", "bank", "last4"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    bank: Mapped[str] = mapped_column(String(16))  # budgetcore.models.Bank value
    last4: Mapped[str] = mapped_column(String(4))
    label: Mapped[str | None] = mapped_column(String(80))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="cards")

    @property
    def display(self) -> str:
        return self.label or f"{self.bank.capitalize()} *{self.last4}"


class RawEmail(Base):
    """Everything that arrives on the inbound domain — the reparse/debug
    archive. `html_body` is Fernet-encrypted when a key is configured."""

    __tablename__ = "raw_emails"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    inbound_address_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inbound_addresses.id"))
    provider_message_id: Mapped[str] = mapped_column(String(100), unique=True)
    from_addr: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    html_body: Mapped[str] = mapped_column(Text, default="")
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # pending | processed | skipped | unrecognized | confirmation | failed
    processing_status: Mapped[str] = mapped_column(String(16), default="pending")
    note: Mapped[str] = mapped_column(Text, default="")  # error text, skip reason, Gmail code


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # System categories are targets of built-in behavior (ATM -> Retiro
    # Efectivo, fallback -> Otros / sin categoría) and cannot be deleted.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    substring: Mapped[str] = mapped_column(String(120))
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"))
    priority: Mapped[int] = mapped_column(Integer, default=100)

    category: Mapped[Category] = relationship()


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    raw_email_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("raw_emails.id"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id"))
    tx_type: Mapped[str] = mapped_column(String(16))  # budgetcore.models.TxType value
    merchant: Mapped[str] = mapped_column(String(200))
    txn_date: Mapped[date] = mapped_column(Date)
    original_amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    fx_rate_used: Mapped[float] = mapped_column(Float, default=1.0)
    amount_dop: Mapped[float] = mapped_column(Float)  # signed: reversals negative
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    dedupe_key: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    card: Mapped[Card] = relationship()
    category: Mapped[Category | None] = relationship()


class CategorySuggestion(Base):
    """An LLM-proposed category for a still-uncategorized transaction.

    Written by the background sweep (services.suggest), shown on the dashboard
    as "Sugerido: X" with accept/dismiss. Accepting applies the category AND
    mints a Rule, so the deterministic rule engine remains the source of truth
    and the LLM only ever handles merchants no rule has seen yet. A separate
    table (not columns on transactions) so `create_all` creates it on deploy —
    create_all never adds columns to an existing table.
    """

    __tablename__ = "category_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id"), unique=True)  # one live suggestion per txn
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"))
    model: Mapped[str] = mapped_column(String(80))  # e.g. "ollama:qwen2.5:3b"
    # Dismissed rows are kept (not deleted) so the sweep doesn't re-suggest
    # the same thing on its next run — the row is the "already asked" marker.
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    category: Mapped[Category] = relationship()


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id", "category_id", "month"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"))
    month: Mapped[date] = mapped_column(Date)  # first of month
    amount_dop: Mapped[float] = mapped_column(Float, default=0.0)

    category: Mapped[Category] = relationship()


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("pair", "effective_date"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    pair: Mapped[str] = mapped_column(String(8), default="USD/DOP")
    rate: Mapped[float] = mapped_column(Float)
    effective_date: Mapped[date] = mapped_column(Date)


class NotificationPref(Base):
    __tablename__ = "notification_prefs"
    __table_args__ = (UniqueConstraint("user_id", "channel"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(16))  # telegram | webpush
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(32))
