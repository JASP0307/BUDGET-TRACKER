"""Data export and account deletion — the user's own controls over their data.

For a product that asks people to forward their bank notifications, "show me
everything you have on me" and "delete all of it" are not compliance chores,
they are the trust argument. Both act only on the calling user's rows.

Deletion is written out table by table rather than relying on cascades, because
the ForeignKeys in models.py declare none: an ORM-level cascade would silently
leave orphans the day a new table is added. The explicit list fails loudly in
review instead.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import (Budget, Card, Category, InboundAddress,
                      NotificationPref, RawEmail, Rule, Transaction, User)


def export_data(session: Session, user: User) -> dict:
    """Everything stored about `user`, as JSON-serializable primitives.

    Raw email bodies are not included: successfully parsed mail is discarded at
    ingestion (services.ingest) and anything still held is a parse failure kept
    only for debugging, so there is nothing durable to hand back.
    """
    cards = {c.id: c for c in session.scalars(
        select(Card).where(Card.user_id == user.id))}
    categories = {c.id: c for c in session.scalars(
        select(Category).where(Category.user_id == user.id))}
    inbound = session.scalars(select(InboundAddress).where(
        InboundAddress.user_id == user.id)).all()
    prefs = session.scalars(select(NotificationPref).where(
        NotificationPref.user_id == user.id)).all()

    transactions = session.scalars(
        select(Transaction).where(Transaction.user_id == user.id)
        .order_by(Transaction.txn_date)).all()
    rules = session.scalars(select(Rule).where(Rule.user_id == user.id)
                            .order_by(Rule.priority)).all()
    budgets = session.scalars(select(Budget).where(Budget.user_id == user.id)
                              .order_by(Budget.month)).all()

    def category_name(category_id):
        cat = categories.get(category_id)
        return cat.name if cat else None

    return {
        "account": {
            "email": user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "timezone": user.timezone,
            "locale": user.locale,
            "forwarding_addresses": [a.token for a in inbound if a.active],
        },
        "cards": [{"bank": c.bank, "last4": c.last4, "label": c.label,
                   "active": c.active} for c in cards.values()],
        "categories": [{"name": c.name, "sort_order": c.sort_order,
                        "is_system": c.is_system} for c in categories.values()],
        "transactions": [{
            "date": t.txn_date.isoformat(),
            "merchant": t.merchant,
            "type": t.tx_type,
            "card": cards[t.card_id].display if t.card_id in cards else None,
            "original_amount": t.original_amount,
            "currency": t.currency,
            "fx_rate_used": t.fx_rate_used,
            "amount_dop": t.amount_dop,
            "category": category_name(t.category_id),
        } for t in transactions],
        "rules": [{"substring": r.substring, "priority": r.priority,
                   "category": category_name(r.category_id)} for r in rules],
        "budgets": [{"month": b.month.isoformat(),
                     "category": category_name(b.category_id),
                     "amount_dop": b.amount_dop} for b in budgets],
        "notifications": [{"channel": p.channel, "enabled": p.enabled,
                           "connected": bool(p.telegram_chat_id)}
                          for p in prefs],
    }


def delete_account(session: Session, user: User) -> None:
    """Erase the user and every row belonging to them. Caller has already
    re-authenticated. Commits.

    Order matters only for readability here (no DB-level cascades to trip), but
    children go before parents so the intent survives if FK constraints are
    tightened later.
    """
    user_id = user.id
    for model in (Transaction, Rule, Budget, NotificationPref, RawEmail,
                  Card, Category, InboundAddress):
        session.execute(delete(model).where(model.user_id == user_id))
    session.execute(delete(User).where(User.id == user_id))
    session.commit()
