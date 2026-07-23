"""Server-rendered pages: month dashboard, transactions, rules, budgets.

Single-tenant: every handler acts on the bootstrap user until Phase 2 auth.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_sessionmaker
from ..models import (Budget, Card, Category, InboundAddress, RawEmail, Rule,
                      Transaction, User)
from ..services.ingest import _next_month
from ..settings import get_settings

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


def _user(session: Session) -> User:
    return session.scalar(select(User).where(
        User.email == get_settings().default_user_email))


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
def home(request: Request):
    month_start = date.today().replace(day=1)
    with get_sessionmaker()() as session:
        user = _user(session)
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
        return templates.TemplateResponse(request, "dashboard.html", {
            "month": month_start, "rows": rows, "recent": recent,
            "categories": categories, "needs_review": needs_review,
            "problem_mail": problem_mail,
            "inbound_addr": f"u_{inbound.token}@{get_settings().inbound_domain}"
                            if inbound else None,
        })


@router.post("/transactions/{txn_id}/category")
def recategorize(txn_id: uuid.UUID, category_id: uuid.UUID = Form(...)):
    with get_sessionmaker()() as session:
        txn = session.get(Transaction, txn_id)
        if txn is not None and session.get(Category, category_id) is not None:
            txn.category_id = category_id
            session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/rules")
def rules_page(request: Request):
    with get_sessionmaker()() as session:
        user = _user(session)
        rules = session.scalars(select(Rule).where(Rule.user_id == user.id)
                                .order_by(Rule.priority)).all()
        categories = session.scalars(select(Category)
                                     .where(Category.user_id == user.id)
                                     .order_by(Category.sort_order)).all()
        return templates.TemplateResponse(request, "rules.html", {
            "rules": rules, "categories": categories})


@router.post("/rules")
def add_rule(substring: str = Form(...), category_id: uuid.UUID = Form(...)):
    with get_sessionmaker()() as session:
        user = _user(session)
        if substring.strip() and session.get(Category, category_id) is not None:
            session.add(Rule(user_id=user.id, substring=substring.strip().upper(),
                             category_id=category_id))
            session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: uuid.UUID):
    with get_sessionmaker()() as session:
        rule = session.get(Rule, rule_id)
        if rule is not None:
            session.delete(rule)
            session.commit()
    return RedirectResponse("/rules", status_code=303)


@router.get("/budgets")
def budgets_page(request: Request):
    month_start = date.today().replace(day=1)
    with get_sessionmaker()() as session:
        user = _user(session)
        return templates.TemplateResponse(request, "budgets.html", {
            "month": month_start,
            "rows": _month_rows(session, user.id, month_start)})


@router.post("/budgets")
async def save_budgets(request: Request):
    month_start = date.today().replace(day=1)
    form = await request.form()
    with get_sessionmaker()() as session:
        user = _user(session)
        for key, value in form.items():
            if not key.startswith("budget_"):
                continue
            try:
                cat_id = uuid.UUID(key.removeprefix("budget_"))
                amount = float(value or 0)
            except ValueError:
                continue
            if session.get(Category, cat_id) is None:
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
