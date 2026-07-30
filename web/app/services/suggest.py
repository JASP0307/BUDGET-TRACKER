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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from budgetcore.categorize import UNCATEGORIZED
from budgetcore.models import TxType

from ..models import (Category, CategorySuggestion, CategorySuggestionMiss,
                      Transaction)
from ..settings import get_settings

log = logging.getLogger("suggest")

# Local model on CPU. The timeout has to cover a *cold* call: Ollama unloads an
# idle model after ~5 minutes, so the first call of each sweep pays the load
# from disk — measured at over 60s on the Lenovo (2 cores, 3.3 GB RAM), while
# warm calls there run 10–15s. The per-run cap keeps a tick comfortably inside
# the 15-minute cron interval even when every transaction is a new merchant.
OLLAMA_TIMEOUT = 150
SWEEP_LIMIT = 20

# A miss only holds while it's this fresh AND was recorded under the model
# currently configured — a model swap (BUDGET_OLLAMA_MODEL) or a category the
# user added since should get a merchant reconsidered, not block it forever.
MISS_TTL_DAYS = 30


@dataclass
class SweepResult:
    created: int
    calls: int  # distinct (user, merchant) pairs actually sent to Ollama

# Worth knowing before editing: the small local models this runs on are very
# sensitive to this prompt. Measured on the real category list with
# qwen2.5:3b, the worked examples below took accuracy from 5/8 to 6/8 — and on
# qwen2.5:1.5b from 0/8 (every merchant collapsed onto one category) to 4/8.
# Abstract instructions alone are not enough at this size; keep the examples.
_SYSTEM_PROMPT = (
    "You label a card transaction with one of the user's budget categories.\n"
    "The merchant text comes from a Dominican bank alert: business name first, "
    "then maybe a branch, street or city. Judge the BUSINESS, ignore the place.\n"
    "Answer with a category from the list only if that business clearly belongs "
    "to it. Otherwise answer null. Wrong is worse than null.\n\n"
    "Examples (with a list containing Combustible, Supermercado, Teléfono, "
    "Suscripciones, Salidas):\n"
    "  \"ESTACION TOTAL NACO\" -> \"Combustible\"   (a filling station)\n"
    "  \"SUPERMERCADO NACIONAL\" -> \"Supermercado\"   (a grocery store)\n"
    "  \"NETFLIX.COM\" -> \"Suscripciones\"   (a streaming subscription)\n"
    "  \"ALTICE PAGO\" -> \"Teléfono\"   (a phone carrier)\n"
    "  \"INVERSIONES DELTA SRL\" -> null   (unknown line of business)"
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
    except (requests.RequestException, KeyError, ValueError, TypeError):
        log.warning("ollama call failed for merchant %r", merchant, exc_info=True)
        return None
    # Not every backend honours `format`: Ollama's cloud-hosted models answer in
    # prose, and a model can also emit a bare `null`, which parses to None. Both
    # once escaped as an AttributeError from .get() and killed the whole sweep,
    # so treat anything that isn't a JSON object as "no answer".
    if not isinstance(answer, dict):
        return None
    name = answer.get("category")
    # Belt and braces on top of the schema enum: only a real category counts.
    return name if name in categories else None


def _uncategorized_txns(session: Session, *, limit: int,
                        current_model: str) -> list[Transaction]:
    """Transactions still waiting for a category and without a suggestion,
    oldest users' newest first. Withdrawals are always auto-categorized at
    ingestion, so only consumo/reversal can be here — no filter needed, but we
    skip RETIRO defensively anyway.

    Also excludes merchants the sweep already asked about and got no answer
    for (CategorySuggestionMiss) — otherwise an abstained merchant has no
    CategorySuggestion row and gets re-sent to Ollama on every single run.
    That exclusion only holds for misses still within MISS_TTL_DAYS and
    recorded under the model configured right now — see MISS_TTL_DAYS."""
    suggested = select(CategorySuggestion.transaction_id)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MISS_TTL_DAYS)
    missed = select(CategorySuggestionMiss.user_id, CategorySuggestionMiss.merchant).where(
        CategorySuggestionMiss.model == current_model,
        CategorySuggestionMiss.checked_at >= cutoff,
    )
    return list(session.scalars(
        select(Transaction)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            (Transaction.category_id.is_(None)) | (Category.name == UNCATEGORIZED),
            Transaction.tx_type != TxType.RETIRO.value,
            Transaction.id.not_in(suggested),
            tuple_(Transaction.user_id,
                  func.upper(func.trim(Transaction.merchant))).not_in(missed),
        )
        .order_by(Transaction.user_id, Transaction.created_at.desc())
        .limit(limit)))


def sweep(session: Session, *, limit: int = SWEEP_LIMIT) -> SweepResult:
    """Suggest categories for pending uncategorized transactions, all users.

    One Ollama call per distinct (user, merchant) in the run — the answer is
    reused for that merchant's siblings. Caller commits.
    """
    settings = get_settings()
    if not settings.ollama_url:
        return SweepResult(created=0, calls=0)

    model_tag = f"ollama:{settings.ollama_model}"
    txns = _uncategorized_txns(session, limit=limit, current_model=model_tag)
    if not txns:
        return SweepResult(created=0, calls=0)

    cat_names: dict = {}    # user_id -> [category names]
    cat_rows: dict = {}     # (user_id, name) -> Category
    answers: dict = {}      # (user_id, MERCHANT) -> name | None
    created = 0
    for txn in txns:
        if txn.user_id not in cat_names:
            # System categories are never valid answers: «Otros / sin
            # categoría» is the state we're trying to leave, and "Retiro
            # Efectivo" is assigned by ingestion from the transaction type,
            # not from the merchant. Offering them just invites wrong picks —
            # the 3b did propose "Retiro Efectivo" for a merchant it didn't
            # recognize.
            rows = session.scalars(
                select(Category).where(Category.user_id == txn.user_id,
                                       Category.is_system.is_(False))
                .order_by(Category.sort_order)).all()
            cat_names[txn.user_id] = [c.name for c in rows]
            cat_rows.update({(txn.user_id, c.name): c for c in rows})

        merchant = (txn.merchant or "").strip().upper()
        key = (txn.user_id, merchant)
        if key not in answers:
            answers[key] = suggest_category(
                txn.merchant or "", cat_names[txn.user_id],
                url=settings.ollama_url, model=settings.ollama_model)
            # A stale miss (past MISS_TTL_DAYS, or from a since-replaced
            # model) isn't filtered out by _uncategorized_txns's query — it's
            # still a row in the table, just not one we exclude on anymore.
            # Refresh it on another miss, or drop it now that it's resolved.
            existing_miss = session.scalar(select(CategorySuggestionMiss).where(
                CategorySuggestionMiss.user_id == txn.user_id,
                CategorySuggestionMiss.merchant == merchant))
            if answers[key] is None:
                if existing_miss is not None:
                    existing_miss.checked_at = datetime.now(timezone.utc)
                    existing_miss.model = model_tag
                else:
                    session.add(CategorySuggestionMiss(
                        user_id=txn.user_id, merchant=merchant, model=model_tag))
            elif existing_miss is not None:
                session.delete(existing_miss)
        name = answers[key]
        if name is None:
            continue
        session.add(CategorySuggestion(
            user_id=txn.user_id,
            transaction_id=txn.id,
            category_id=cat_rows[(txn.user_id, name)].id,
            model=model_tag))
        created += 1
    return SweepResult(created=created, calls=len(answers))


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
