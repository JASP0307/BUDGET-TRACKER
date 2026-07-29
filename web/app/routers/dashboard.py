"""Server-rendered pages: month dashboard, transactions, rules, budgets.

Every handler acts on the logged-in user (auth.deps.current_user); queries are
scoped by user_id so one account can never read or mutate another's data.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from ..templating import templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from budgetcore.categorize import UNCATEGORIZED

from ..auth.deps import current_user, optional_user
from ..db import get_sessionmaker
from ..i18n import normalize_lang
from ..models import (Budget, Card, Category, CategorySuggestion,
                      InboundAddress, RawEmail, Rule, Transaction, User)
from ..services.inbound import format_inbound_address
from ..services.ingest import _next_month, recategorize_uncategorized
from ..services.suggest import prune_stale

router = APIRouter()


def _month_rows(session: Session, user_id, month_start: date) -> list[dict]:
    spent_by_cat = dict(session.execute(
        select(Transaction.category_id,
               func.coalesce(func.sum(Transaction.amount_dop), 0.0))
        .where(Transaction.user_id == user_id,
               Transaction.txn_date >= month_start,
               Transaction.txn_date < _next_month(month_start))
        .group_by(Transaction.category_id)).all())
    budgets = {b.category_id: b.amount_dop for b in session.scalars(
        select(Budget).where(Budget.user_id == user_id,
                             Budget.month == month_start))}
    rows = []
    for cat in session.scalars(select(Category).where(Category.user_id == user_id)
                               .order_by(Category.sort_order)):
        spent = float(spent_by_cat.get(cat.id, 0.0))
        budget = float(budgets.get(cat.id, 0.0))
        pct = (spent / budget * 100) if budget > 0 else None
        rows.append({"category": cat, "spent": spent, "budget": budget,
                     "remaining": budget - spent, "pct": pct})
    return rows


@router.get("/")
def home(request: Request, user: User = Depends(current_user)):
    month_start = date.today().replace(day=1)
    with get_sessionmaker()() as session:
        # First-time users have no transactions yet — send them to onboarding
        # instead of an empty dashboard. "No transactions ever" is the live
        # signal for "still connecting", so no extra flag is needed.
        any_txn = session.scalar(select(Transaction.id)
                                 .where(Transaction.user_id == user.id).limit(1))
        if any_txn is None:
            return RedirectResponse("/setup", status_code=303)
        rows = _month_rows(session, user.id, month_start)
        recent = session.scalars(
            select(Transaction).where(Transaction.user_id == user.id)
            .order_by(Transaction.txn_date.desc(), Transaction.created_at.desc())
            .limit(20)).all()
        categories = session.scalars(select(Category)
                                     .where(Category.user_id == user.id)
                                     .order_by(Category.sort_order)).all()
        needs_review = session.scalar(select(func.count()).select_from(Card).where(
            Card.user_id == user.id, Card.needs_review.is_(True))) or 0
        problem_mail = session.scalar(select(func.count()).select_from(RawEmail).where(
            RawEmail.processing_status.in_(["unrecognized", "failed"]))) or 0
        inbound = session.scalar(select(InboundAddress).where(
            InboundAddress.user_id == user.id, InboundAddress.active.is_(True)))
        # LLM-proposed categories for still-uncategorized transactions,
        # keyed by transaction id for the template's accept/dismiss chips.
        suggestions = {s.transaction_id: s for s in session.scalars(
            select(CategorySuggestion)
            .where(CategorySuggestion.user_id == user.id,
                   CategorySuggestion.dismissed.is_(False)))}
        return templates.TemplateResponse(request, "dashboard.html", {
            "month": month_start, "rows": rows, "recent": recent,
            "categories": categories, "needs_review": needs_review,
            "problem_mail": problem_mail, "suggestions": suggestions,
            "uncategorized": UNCATEGORIZED,
            "inbound_addr": format_inbound_address(inbound.token) if inbound else None,
        })


@router.get("/lang/{code}")
def set_language(code: str, request: Request,
                 user: User | None = Depends(optional_user)):
    """Switch UI language. Stores a cookie the templates read, and persists it
    to User.locale when signed in so it follows the account across devices.
    Public so it works on the login/register pages too."""
    lang = normalize_lang(code)
    nxt = request.query_params.get("next", "/")
    if not nxt.startswith("/"):  # only local redirects, never open-redirect
        nxt = "/"
    response = RedirectResponse(nxt, status_code=303)
    response.set_cookie("lang", lang, max_age=60 * 60 * 24 * 365,
                        samesite="lax", path="/")
    if user is not None:
        with get_sessionmaker()() as session:
            db_user = session.get(User, user.id)
            if db_user is not None:
                db_user.locale = lang
                session.commit()
    return response


@router.post("/transactions/{txn_id}/category")
def recategorize(txn_id: uuid.UUID, category_id: uuid.UUID = Form(...),
                 user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        txn = session.get(Transaction, txn_id)
        category = session.get(Category, category_id)
        if (txn is not None and txn.user_id == user.id
                and category is not None and category.user_id == user.id):
            txn.category_id = category_id
            # A hand-picked category supersedes any pending LLM suggestion.
            sugg = session.scalar(select(CategorySuggestion).where(
                CategorySuggestion.transaction_id == txn.id))
            if sugg is not None:
                session.delete(sugg)
            session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/transactions/{txn_id}/accept-suggestion")
def accept_suggestion(txn_id: uuid.UUID, user: User = Depends(current_user)):
    """Apply the LLM's proposed category AND mint a rule for the merchant, so
    the rule engine handles this merchant deterministically from now on. The
    fresh rule is retro-applied to sibling uncategorized transactions — same
    flow as adding a rule by hand on /rules."""
    with get_sessionmaker()() as session:
        sugg = session.scalar(select(CategorySuggestion).where(
            CategorySuggestion.transaction_id == txn_id,
            CategorySuggestion.user_id == user.id))
        txn = session.get(Transaction, txn_id)
        category = session.get(Category, sugg.category_id) if sugg else None
        if (sugg is not None and txn is not None and txn.user_id == user.id
                and category is not None and category.user_id == user.id):
            txn.category_id = category.id
            substring = (txn.merchant or "").strip().upper()[:120]
            exists = substring and session.scalar(select(Rule).where(
                Rule.user_id == user.id, Rule.substring == substring))
            if substring and not exists:
                session.add(Rule(user_id=user.id, substring=substring,
                                 category_id=category.id))
            session.delete(sugg)
            session.flush()  # make the new rule visible to the retro-apply pass
            recategorize_uncategorized(session, user.id)
            prune_stale(session, user.id)  # siblings just left "uncategorized"
            session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/transactions/{txn_id}/rule")
def rule_from_transaction(txn_id: uuid.UUID, substring: str = Form(...),
                          category_id: uuid.UUID = Form(...),
                          user: User = Depends(current_user)):
    """Mint a rule from a transaction row on the dashboard — the by-hand twin of
    accept-suggestion. The merchant arrives prefilled and editable (bank strings
    carry store numbers nobody wants in a rule), and the new rule is retro-applied
    to sibling uncategorized transactions, same as adding one on /rules.

    The originating transaction is moved explicitly: the retro-apply only touches
    uncategorized rows, and this one may well have been categorized by hand
    already — the point of the button is "and from now on, always this".
    """
    substring = substring.strip().upper()[:120]
    with get_sessionmaker()() as session:
        txn = session.get(Transaction, txn_id)
        category = session.get(Category, category_id)
        # A rule pointing at the fallback category says "leave this where things
        # land with no rule at all" — a no-op that would still shadow every later
        # rule for the same merchant, since the first match wins.
        if category is not None and category.name == UNCATEGORIZED:
            return RedirectResponse("/", status_code=303)
        if (substring and txn is not None and txn.user_id == user.id
                and category is not None and category.user_id == user.id):
            existing = session.scalar(select(Rule).where(
                Rule.user_id == user.id, Rule.substring == substring))
            if existing is None:
                session.add(Rule(user_id=user.id, substring=substring,
                                 category_id=category.id))
            else:  # same merchant, new mind — retarget instead of duplicating
                existing.category_id = category.id
            txn.category_id = category.id
            sugg = session.scalar(select(CategorySuggestion).where(
                CategorySuggestion.transaction_id == txn.id))
            if sugg is not None:
                session.delete(sugg)
            session.flush()  # make the new rule visible to the retro-apply pass
            recategorize_uncategorized(session, user.id)
            prune_stale(session, user.id)
            session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/transactions/{txn_id}/dismiss-suggestion")
def dismiss_suggestion(txn_id: uuid.UUID, user: User = Depends(current_user)):
    """Hide a suggestion. The row is kept (flagged) so the sweep won't ask the
    model about this transaction again."""
    with get_sessionmaker()() as session:
        sugg = session.scalar(select(CategorySuggestion).where(
            CategorySuggestion.transaction_id == txn_id,
            CategorySuggestion.user_id == user.id))
        if sugg is not None:
            sugg.dismissed = True
            session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/rules")
def rules_page(request: Request, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        rules = session.scalars(select(Rule).where(Rule.user_id == user.id)
                                .order_by(Rule.priority)).all()
        categories = session.scalars(select(Category)
                                     .where(Category.user_id == user.id)
                                     .order_by(Category.sort_order)).all()
        return templates.TemplateResponse(request, "rules.html", {
            "rules": rules, "categories": categories})


@router.post("/rules")
def add_rule(substring: str = Form(...), category_id: uuid.UUID = Form(...),
             user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        category = session.get(Category, category_id)
        if (substring.strip() and category is not None
                and category.user_id == user.id):
            session.add(Rule(user_id=user.id, substring=substring.strip().upper(),
                             category_id=category_id))
            session.flush()  # make the new rule visible to the retro-apply pass
            recategorize_uncategorized(session, user.id)
            session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: uuid.UUID, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        rule = session.get(Rule, rule_id)
        if rule is not None and rule.user_id == user.id:
            session.delete(rule)
            session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.get("/categories")
def categories_page(request: Request, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        cats = session.scalars(select(Category).where(Category.user_id == user.id)
                               .order_by(Category.sort_order)).all()
        # How many transactions each holds — context before a delete.
        counts = dict(session.execute(
            select(Transaction.category_id, func.count())
            .where(Transaction.user_id == user.id)
            .group_by(Transaction.category_id)).all())
        return templates.TemplateResponse(request, "categories.html", {
            "categories": cats,
            "counts": {c.id: int(counts.get(c.id, 0)) for c in cats}})


@router.post("/categories")
def add_category(name: str = Form(...), user: User = Depends(current_user)):
    name = name.strip()[:80]
    with get_sessionmaker()() as session:
        if name:
            # Case-insensitive de-dupe so "Renta"/"renta" don't both exist.
            exists = session.scalar(select(Category).where(
                Category.user_id == user.id,
                func.lower(Category.name) == name.lower()))
            if exists is None:
                max_order = session.scalar(select(func.max(Category.sort_order))
                                           .where(Category.user_id == user.id)) or 0
                session.add(Category(user_id=user.id, name=name,
                                     sort_order=max_order + 1))
                session.commit()
    return RedirectResponse("/categories", status_code=303)


@router.post("/categories/{category_id}/delete")
def delete_category(category_id: uuid.UUID, user: User = Depends(current_user)):
    """Remove a user category. Its transactions fall back to «Otros / sin
    categoría»; rules and budgets that targeted it are dropped (both reference a
    category that no longer exists). System categories cannot be deleted."""
    with get_sessionmaker()() as session:
        cat = session.get(Category, category_id)
        if cat is None or cat.user_id != user.id or cat.is_system:
            return RedirectResponse("/categories", status_code=303)
        fallback = session.scalar(select(Category).where(
            Category.user_id == user.id, Category.name == UNCATEGORIZED))
        for txn in session.scalars(select(Transaction).where(
                Transaction.user_id == user.id,
                Transaction.category_id == cat.id)):
            txn.category_id = fallback.id if fallback else None
        for rule in session.scalars(select(Rule).where(
                Rule.user_id == user.id, Rule.category_id == cat.id)):
            session.delete(rule)
        for budget in session.scalars(select(Budget).where(
                Budget.user_id == user.id, Budget.category_id == cat.id)):
            session.delete(budget)
        session.delete(cat)
        session.commit()
    return RedirectResponse("/categories", status_code=303)


@router.get("/budgets")
def budgets_page(request: Request, user: User = Depends(current_user)):
    month_start = date.today().replace(day=1)
    with get_sessionmaker()() as session:
        return templates.TemplateResponse(request, "budgets.html", {
            "month": month_start,
            "rows": _month_rows(session, user.id, month_start)})


@router.post("/budgets")
async def save_budgets(request: Request, user: User = Depends(current_user)):
    month_start = date.today().replace(day=1)
    form = await request.form()
    with get_sessionmaker()() as session:
        for key, value in form.items():
            if not key.startswith("budget_"):
                continue
            try:
                cat_id = uuid.UUID(key.removeprefix("budget_"))
                amount = float(value or 0)
            except ValueError:
                continue
            category = session.get(Category, cat_id)
            if category is None or category.user_id != user.id:
                continue
            row = session.scalar(select(Budget).where(
                Budget.user_id == user.id, Budget.category_id == cat_id,
                Budget.month == month_start))
            if row is None:
                session.add(Budget(user_id=user.id, category_id=cat_id,
                                   month=month_start, amount_dop=amount))
            else:
                row.amount_dop = amount
        session.commit()
    return RedirectResponse("/budgets", status_code=303)
