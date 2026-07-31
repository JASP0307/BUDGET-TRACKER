"""Server-rendered pages: month dashboard, transactions, rules, budgets.

Every handler acts on the logged-in user (auth.deps.current_user); queries are
scoped by user_id so one account can never read or mutate another's data.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from ..templating import templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from budgetcore.categorize import UNCATEGORIZED, categorize
from budgetcore.models import Bank, TxType
from budgetcore.models import Transaction as CoreTxn

from ..auth.deps import current_user, optional_user
from ..db import get_sessionmaker
from ..i18n import normalize_lang
from ..models import (Budget, Card, Category, CategorySuggestion,
                      InboundAddress, RawEmail, Rule, Transaction, User)
from ..services import notify
from ..services.inbound import format_inbound_address
from ..services.ingest import (_next_month, _rules_dict, current_fx_rate,
                               dedupe_key_for, get_or_create_manual_card,
                               month_spend_and_budget,
                               recategorize_uncategorized)
from ..services.suggest import prune_stale

log = logging.getLogger("dashboard")

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


# --- Manual transactions ---------------------------------------------------
#
# Spend no bank emailed about — cash, a transfer to a person — typed in by hand.
# The row is deliberately indistinguishable from an ingested one: same rules,
# same content dedupe key (so a bank email arriving later collapses onto it
# instead of double-counting), same Telegram alert. `raw_email_id IS NULL` is
# what marks a row manual, which is why edit and delete need no new column.

MANUAL_CARD_CHOICE = "manual"  # the "Efectivo / otro" option's <select> value
_CURRENCIES = ("RD$", "US$")
_DUPLICATE_ERROR = ("Ya tienes una transacción idéntica: mismo comercio, fecha, "
                    "monto y cuenta. Si de verdad es un cargo aparte, márcalo "
                    "abajo.")


def _form_values(txn: Transaction | None) -> dict[str, str]:
    """The form's fields as strings, either blank-with-defaults or read off an
    existing transaction. The POST handlers echo the *submitted* strings back
    on error instead, so nothing the user typed is lost."""
    if txn is None:
        return {"merchant": "", "txn_date": date.today().isoformat(),
                "original_amount": "", "currency": "RD$",
                "tx_type": "consumo", "card": "", "category_id": ""}
    return {
        "merchant": txn.merchant,
        "txn_date": txn.txn_date.isoformat(),
        "original_amount": f"{txn.original_amount:.2f}",
        "currency": txn.currency,
        "tx_type": txn.tx_type,
        "card": (MANUAL_CARD_CHOICE if txn.card.bank == Bank.MANUAL.value
                 else str(txn.card_id)),
        "category_id": str(txn.category_id) if txn.category_id else "",
    }


def _form_context(session: Session, user: User, *,
                  txn: Transaction | None = None, submitted: dict | None = None,
                  error: str | None = None, allow_separate: bool = False) -> dict:
    # The manual bucket is excluded from the picker and re-offered as a fixed
    # option below it, so it can't appear twice once it has been created.
    cards = session.scalars(
        select(Card).where(Card.user_id == user.id, Card.active.is_(True),
                           Card.bank != Bank.MANUAL.value)
        .order_by(Card.bank, Card.last4)).all()
    categories = session.scalars(select(Category)
                                 .where(Category.user_id == user.id)
                                 .order_by(Category.sort_order)).all()
    return {"txn": txn, "cards": cards, "categories": categories,
            "form": submitted if submitted is not None else _form_values(txn),
            "error": error, "allow_separate": allow_separate,
            "manual_choice": MANUAL_CARD_CHOICE}


def _parse_entry(form: dict[str, str]) -> tuple[dict | None, str | None]:
    """Coerce the raw form strings into typed values. Returns (parsed, error);
    exactly one is set. Done by hand rather than with typed Form() params so a
    fat-fingered amount comes back as the page's own error banner instead of
    FastAPI's raw 422."""
    merchant = form["merchant"].strip()[:200]
    if not merchant:
        return None, "Escribe el comercio o la persona."
    try:
        txn_date = date.fromisoformat(form["txn_date"])
    except ValueError:
        return None, "Fecha inválida."
    try:  # tolerate the thousands separators a copy-paste brings along
        amount = round(float(form["original_amount"].replace(",", "").strip()), 2)
    except ValueError:
        return None, "Monto inválido."
    if amount <= 0:
        return None, "El monto debe ser mayor que cero."
    if form["currency"] not in _CURRENCIES:
        return None, "Moneda inválida."
    try:
        tx_type = TxType(form["tx_type"])
    except ValueError:
        return None, "Tipo inválido."
    return {"merchant": merchant, "txn_date": txn_date, "amount": amount,
            "currency": form["currency"], "tx_type": tx_type}, None


def _resolve_card(session: Session, user: User, choice: str) -> Card | None:
    if choice == MANUAL_CARD_CHOICE:
        return get_or_create_manual_card(session, user.id)
    try:
        card = session.get(Card, uuid.UUID(choice))
    except ValueError:
        return None
    return card if card is not None and card.user_id == user.id else None


def _build_core(session: Session, user: User, card: Card, parsed: dict,
                category: Category | None) -> tuple[CoreTxn, float, Category | None]:
    """The budgetcore transaction behind a manual entry. One object drives
    categorization, the signed amount, the dedupe key and the Telegram message,
    exactly as the parsed email does on the ingest path."""
    fx = current_fx_rate(session) if parsed["currency"] == "US$" else 1.0
    core = CoreTxn(
        message_id=f"manual:{uuid.uuid4()}", bank=Bank(card.bank),
        last4=card.last4, tx_type=parsed["tx_type"], txn_date=parsed["txn_date"],
        merchant=parsed["merchant"], amount_dop=parsed["amount"] * fx,
        original_amount=parsed["amount"], currency=parsed["currency"],
        card=card.display)
    if category is not None:  # the user named one; take it as final
        return replace(core, category=category.name), fx, category
    # "Automática": the same rules ingestion applies, which also hands ATM
    # withdrawals to Retiro Efectivo and everything unmatched to the fallback.
    core = categorize(core, _rules_dict(session, user.id))
    resolved = session.scalar(select(Category).where(
        Category.user_id == user.id, Category.name == core.category))
    return core, fx, resolved


def _key_taken(session: Session, user_id, key: str, exclude_id=None) -> bool:
    stmt = select(Transaction.id).where(Transaction.user_id == user_id,
                                        Transaction.dedupe_key == key)
    if exclude_id is not None:
        stmt = stmt.where(Transaction.id != exclude_id)
    return session.scalar(stmt) is not None


def _salt_key(session: Session, user_id, key: str, exclude_id=None) -> str:
    """A free variant of `key`, for when the user has confirmed this really is a
    second, separate charge with the same merchant, date, amount and card. The
    salt is what stops the unique constraint from rejecting it — and what makes
    the choice deliberate rather than a silent duplicate."""
    n = 2
    while _key_taken(session, user_id, f"{key}|#{n}", exclude_id):
        n += 1
    return f"{key}|#{n}"


def _manual_txn(session: Session, user: User, txn_id) -> Transaction | None:
    """The user's own transaction, and only if it was entered by hand. An
    email-sourced row is reproducible from its source and must not be edited
    into something the bank never said."""
    txn = session.get(Transaction, txn_id)
    if txn is None or txn.user_id != user.id or txn.raw_email_id is not None:
        return None
    return txn


def _submitted(merchant, txn_date, original_amount, currency, tx_type,
               card, category_id) -> dict[str, str]:
    return {"merchant": merchant, "txn_date": txn_date,
            "original_amount": original_amount, "currency": currency,
            "tx_type": tx_type, "card": card, "category_id": category_id}


@router.get("/transactions/new")
def new_transaction(request: Request, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        return templates.TemplateResponse(request, "transaction_form.html",
                                          _form_context(session, user))


@router.post("/transactions/new")
def create_transaction(request: Request, merchant: str = Form(""),
                       txn_date: str = Form(""), original_amount: str = Form(""),
                       currency: str = Form(""), tx_type: str = Form(""),
                       card: str = Form(""), category_id: str = Form(""),
                       separate: str = Form(""),
                       user: User = Depends(current_user)):
    submitted = _submitted(merchant, txn_date, original_amount, currency,
                           tx_type, card, category_id)
    with get_sessionmaker()() as session:
        def refuse(error: str, allow_separate: bool = False):
            return templates.TemplateResponse(
                request, "transaction_form.html",
                _form_context(session, user, submitted=submitted, error=error,
                              allow_separate=allow_separate))

        parsed, error = _parse_entry(submitted)
        if parsed is None:
            return refuse(error)
        chosen_card = _resolve_card(session, user, card)
        if chosen_card is None:
            return refuse("Elige una cuenta o tarjeta.")
        chosen_category, error = _resolve_category(session, user, category_id)
        if error:
            return refuse(error)

        core, fx, category = _build_core(session, user, chosen_card, parsed,
                                         chosen_category)
        key = dedupe_key_for(chosen_card, core.txn_date, core.merchant,
                             core.signed_amount())
        if _key_taken(session, user.id, key):
            if not separate:
                return refuse(_DUPLICATE_ERROR, allow_separate=True)
            key = _salt_key(session, user.id, key)

        session.add(Transaction(
            user_id=user.id, raw_email_id=None, card_id=chosen_card.id,
            tx_type=core.tx_type.value, merchant=core.merchant,
            txn_date=core.txn_date, original_amount=core.original_amount,
            currency=core.currency, fx_rate_used=fx,
            amount_dop=core.signed_amount(),
            category_id=category.id if category else None, dedupe_key=key))
        session.commit()
        _notify_added(session, user.id, core, category)
    return RedirectResponse("/", status_code=303)


@router.get("/transactions/{txn_id}/edit")
def edit_transaction(txn_id: uuid.UUID, request: Request,
                     user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        txn = _manual_txn(session, user, txn_id)
        if txn is None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "transaction_form.html",
                                          _form_context(session, user, txn=txn))


@router.post("/transactions/{txn_id}/edit")
def update_transaction(txn_id: uuid.UUID, request: Request,
                       merchant: str = Form(""), txn_date: str = Form(""),
                       original_amount: str = Form(""), currency: str = Form(""),
                       tx_type: str = Form(""), card: str = Form(""),
                       category_id: str = Form(""), separate: str = Form(""),
                       user: User = Depends(current_user)):
    submitted = _submitted(merchant, txn_date, original_amount, currency,
                           tx_type, card, category_id)
    with get_sessionmaker()() as session:
        txn = _manual_txn(session, user, txn_id)
        if txn is None:
            return RedirectResponse("/", status_code=303)

        def refuse(error: str, allow_separate: bool = False):
            return templates.TemplateResponse(
                request, "transaction_form.html",
                _form_context(session, user, txn=txn, submitted=submitted,
                              error=error, allow_separate=allow_separate))

        parsed, error = _parse_entry(submitted)
        if parsed is None:
            return refuse(error)
        chosen_card = _resolve_card(session, user, card)
        if chosen_card is None:
            return refuse("Elige una cuenta o tarjeta.")
        chosen_category, error = _resolve_category(session, user, category_id)
        if error:
            return refuse(error)

        core, fx, category = _build_core(session, user, chosen_card, parsed,
                                         chosen_category)
        key = dedupe_key_for(chosen_card, core.txn_date, core.merchant,
                             core.signed_amount())
        if _key_taken(session, user.id, key, exclude_id=txn.id):
            if not separate:
                return refuse(_DUPLICATE_ERROR, allow_separate=True)
            key = _salt_key(session, user.id, key, exclude_id=txn.id)

        txn.card_id = chosen_card.id
        txn.tx_type = core.tx_type.value
        txn.merchant = core.merchant
        txn.txn_date = core.txn_date
        txn.original_amount = core.original_amount
        txn.currency = core.currency
        txn.fx_rate_used = fx
        txn.amount_dop = core.signed_amount()
        txn.category_id = category.id if category else None
        txn.dedupe_key = key
        session.flush()
        prune_stale(session, user.id)  # a filed row needs no LLM suggestion
        session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/transactions/{txn_id}/delete")
def delete_transaction(txn_id: uuid.UUID, user: User = Depends(current_user)):
    with get_sessionmaker()() as session:
        txn = _manual_txn(session, user, txn_id)
        if txn is not None:
            # The suggestion's FK to transactions is NOT NULL, and the sweep
            # does reach hand-entered rows that landed uncategorized.
            sugg = session.scalar(select(CategorySuggestion).where(
                CategorySuggestion.transaction_id == txn.id))
            if sugg is not None:
                session.delete(sugg)
            session.delete(txn)
            session.commit()
    return RedirectResponse("/", status_code=303)


def _resolve_category(session: Session, user: User,
                      category_id: str) -> tuple[Category | None, str | None]:
    """(category, error). An empty value means "Automática" — no category and no
    error, so the rules decide."""
    if not category_id:
        return None, None
    try:
        category = session.get(Category, uuid.UUID(category_id))
    except ValueError:
        return None, "Categoría inválida."
    if category is None or category.user_id != user.id:
        return None, "Categoría inválida."
    return category, None


def _notify_added(session: Session, user_id, core: CoreTxn,
                  category: Category | None) -> None:
    """The same alert the email path sends: a manual entry is still spend, and
    the remaining-budget line is the whole reason to send it. Best effort — a
    Telegram outage must not lose a transaction already committed."""
    try:
        spent, budget = month_spend_and_budget(
            session, user_id, category.id if category else None, core.txn_date)
        notify.transaction_alert(
            session, user_id, core, spent, budget,
            category_id=category.id if category else None,
            category_name=category.name if category else None)
    except Exception:  # noqa: BLE001
        log.exception("manual transaction alert failed for user %s", user_id)


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
