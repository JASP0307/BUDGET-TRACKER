"""LLM category suggestions for uncategorized transactions.

Rules stay the deterministic first pass at ingestion; this module only sees
what the rules didn't match. A background sweep (scripts/suggest_categories.py)
asks a local Ollama model to pick one of the *user's own* categories for each
uncategorized merchant and stores the answer as a CategorySuggestion row. The
dashboard renders it as "Sugerido: X" with accept/dismiss; accepting also mints
a Rule so the same merchant never needs the LLM again.

The model runs locally (old-laptop CPU, seconds per call), which is why this is
a batch job and never part of the ingestion webhook. Everything here is best
effort: an unreachable Ollama, a timeout, or a garbage answer just means no
suggestion — mirroring notify.py's "must never break the pipeline" stance.
"""

from __future__ import annotations

import json
import logging

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from budgetcore.categorize import UNCATEGORIZED
from budgetcore.models import TxType

from ..models import Category, CategorySuggestion, Transaction
from ..settings import get_settings

log = logging.getLogger("suggest")

# Local model on CPU: generous per-call timeout, small per-run cap so a cron
# tick finishes well before the next one starts.
OLLAMA_TIMEOUT = 60
SWEEP_LIMIT = 50

_SYSTEM_PROMPT = (
    "You classify personal credit/debit card transactions from the Dominican "
    "Republic into the user's own budget categories. Merchant names come from "
    "bank notification emails and are often abbreviated, uppercase, or in "
    "Spanish. The text usually starts with the business name, often followed "
    "by a branch, street, or city — ignore that location part; classify by "
    "the business only. Pick a category ONLY when the business clearly "
    "belongs to it. If its line of business matches no listed category, or "
    "you don't recognize the business, answer null. A wrong category is "
    "worse than no answer."
)


def _schema(categories: list[str]) -> dict:
    """JSON schema for Ollama structured output: the answer must be one of the
    user's category names, or null for "not sure"."""
    return {
        "type": "object",
        "properties": {
            "category": {"type": ["string", "null"], "enum": [*categories, None]},
        },
        "required": ["category"],
    }


def suggest_category(merchant: str, categories: list[str], *,
                     url: str, model: str) -> str | None:
    """Ask the local model for a category. Returns a name from `categories`,
    or None (model unsure, bad answer, or Ollama unreachable)."""
    if not merchant.strip() or not categories:
        return None
    try:
        resp = requests.post(
            f"{url}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": _schema(categories),
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content":
                        "Categories:\n"
                        + "\n".join(f"- {c}" for c in categories)
                        + f"\n\nTransaction merchant: {merchant.strip()}"},
                ],
            },
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        answer = json.loads(resp.json()["message"]["content"])
        name = answer.get("category")
    except (requests.RequestException, KeyError, ValueError, TypeError):
        log.warning("ollama call failed for merchant %r", merchant, exc_info=True)
        return None
    # Belt and braces on top of the schema enum: only a real category counts.
    return name if name in categories else None


def _uncategorized_txns(session: Session, *, limit: int) -> list[Transaction]:
    """Transactions still waiting for a category and without a suggestion,
    oldest users' newest first. Withdrawals are always auto-categorized at
    ingestion, so only consumo/reversal can be here — no filter needed, but we
    skip RETIRO defensively anyway."""
    suggested = select(CategorySuggestion.transaction_id)
    return list(session.scalars(
        select(Transaction)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            (Transaction.category_id.is_(None)) | (Category.name == UNCATEGORIZED),
            Transaction.tx_type != TxType.RETIRO.value,
            Transaction.id.not_in(suggested),
        )
        .order_by(Transaction.user_id, Transaction.created_at.desc())
        .limit(limit)))


def sweep(session: Session, *, limit: int = SWEEP_LIMIT) -> int:
    """Suggest categories for pending uncategorized transactions, all users.

    One Ollama call per distinct (user, merchant) in the run — the answer is
    reused for that merchant's siblings. Returns the number of suggestion rows
    created. Caller commits.
    """
    settings = get_settings()
    if not settings.ollama_url:
        return 0

    txns = _uncategorized_txns(session, limit=limit)
    if not txns:
        return 0

    cat_names: dict = {}    # user_id -> [category names]
    cat_rows: dict = {}     # (user_id, name) -> Category
    answers: dict = {}      # (user_id, MERCHANT) -> name | None
    created = 0
    for txn in txns:
        if txn.user_id not in cat_names:
            rows = session.scalars(
                select(Category).where(Category.user_id == txn.user_id,
                                       Category.name != UNCATEGORIZED)
                .order_by(Category.sort_order)).all()
            cat_names[txn.user_id] = [c.name for c in rows]
            cat_rows.update({(txn.user_id, c.name): c for c in rows})

        key = (txn.user_id, (txn.merchant or "").strip().upper())
        if key not in answers:
            answers[key] = suggest_category(
                txn.merchant or "", cat_names[txn.user_id],
                url=settings.ollama_url, model=settings.ollama_model)
        name = answers[key]
        if name is None:
            continue
        session.add(CategorySuggestion(
            user_id=txn.user_id,
            transaction_id=txn.id,
            category_id=cat_rows[(txn.user_id, name)].id,
            model=f"ollama:{settings.ollama_model}"))
        created += 1
    return created


def prune_stale(session: Session, user_id) -> int:
    """Drop suggestions whose transaction is no longer uncategorized — e.g.
    after a rule-driven retro-apply moved the sibling transactions, or the user
    recategorized by hand. Returns rows removed. Caller commits."""
    removed = 0
    for sugg in session.scalars(
            select(CategorySuggestion)
            .join(Transaction, CategorySuggestion.transaction_id == Transaction.id)
            .outerjoin(Category, Transaction.category_id == Category.id)
            .where(CategorySuggestion.user_id == user_id,
                   Transaction.category_id.is_not(None),
                   Category.name != UNCATEGORIZED)):
        session.delete(sugg)
        removed += 1
    return removed
