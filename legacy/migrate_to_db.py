"""One-off migration: Google Sheet + config.toml -> the web app's database.

Creates the developer's account as user #1 with cards, categories, rules,
budgets (current month), FX rate, Telegram pref, and every Sheet transaction
(dedupe keys recomputed with the shared signature). Idempotent: rows that
already exist are left alone, so it can re-run during the parallel phase.

Run from the repo root:
    PYTHONPATH=core:legacy:. .venv/bin/python legacy/migrate_to_db.py [--config config.toml]
"""

from __future__ import annotations

import argparse
from datetime import date

from sqlalchemy import select

from budgetcore.dedupe import signature
from budgettracker import config as config_mod
from budgettracker import sheets
from budgettracker.cards import _LABELS
from budgettracker.main import _credentials
from web.app.db import Base, get_engine, get_sessionmaker
from web.app.models import (Budget, Card, Category, FxRate, NotificationPref,
                            Rule, Transaction)
from web.app.services.seed import SYSTEM_CATEGORIES, bootstrap


def migrate(config_path: str) -> None:
    cfg = config_mod.load(config_path)
    Base.metadata.create_all(get_engine())

    with get_sessionmaker()() as s:
        user = bootstrap(s)

        # Cards: the three known ones, labeled, pre-reviewed.
        label_by_key = {}
        for (bank, last4), label in _LABELS.items():
            card = s.scalar(select(Card).where(
                Card.user_id == user.id, Card.bank == bank.value, Card.last4 == last4))
            if card is None:
                card = Card(user_id=user.id, bank=bank.value, last4=last4,
                            label=label, needs_review=False)
                s.add(card)
                s.flush()
            label_by_key[label] = card

        # Categories: everything the config budgets, plus the system pair.
        cats: dict[str, Category] = {}
        order = 0
        for name in list(cfg.budget) + SYSTEM_CATEGORIES:
            if name in cats:
                continue
            cat = s.scalar(select(Category).where(
                Category.user_id == user.id, Category.name == name))
            if cat is None:
                cat = Category(user_id=user.id, name=name, sort_order=order,
                               is_system=name in SYSTEM_CATEGORIES)
                s.add(cat)
                s.flush()
            cats[name] = cat
            order += 1

        # Rules, budgets (current month), FX rate, Telegram pref.
        for substring, cat_name in cfg.rules.items():
            if cat_name in cats and s.scalar(select(Rule).where(
                    Rule.user_id == user.id, Rule.substring == substring)) is None:
                s.add(Rule(user_id=user.id, substring=substring,
                           category_id=cats[cat_name].id))
        month = date.today().replace(day=1)
        for cat_name, amount in cfg.budget.items():
            if s.scalar(select(Budget).where(
                    Budget.user_id == user.id, Budget.month == month,
                    Budget.category_id == cats[cat_name].id)) is None:
                s.add(Budget(user_id=user.id, category_id=cats[cat_name].id,
                             month=month, amount_dop=amount))
        if s.scalar(select(FxRate).where(FxRate.pair == "USD/DOP")) is None:
            s.add(FxRate(pair="USD/DOP", rate=cfg.usd_to_dop, effective_date=date.today()))
        if s.scalar(select(NotificationPref).where(
                NotificationPref.user_id == user.id,
                NotificationPref.channel == "telegram")) is None:
            s.add(NotificationPref(user_id=user.id, channel="telegram",
                                   telegram_chat_id=cfg.telegram_chat_id))
        s.commit()

        # Transactions from the Sheet.
        creds = _credentials(cfg.gmail_credentials_file, cfg.gmail_token_file)
        sheet = sheets.get_service(creds)
        resp = sheet.spreadsheets().values().get(
            spreadsheetId=cfg.spreadsheet_id,
            range=f"{cfg.transactions_tab}!A2:I",
            valueRenderOption="UNFORMATTED_VALUE").execute()

        imported = skipped = 0
        for row in resp.get("values", []):
            if len(row) < 9:
                continue
            msg_id, raw_date, card_label, tx_type, merchant = row[0], row[1], row[2], row[3], row[4]
            category_name, amount, original, currency = row[5], row[6], row[7], row[8]
            txn_date = sheets._cell_date(raw_date)
            card = label_by_key.get(card_label)
            if txn_date is None or card is None:
                print(f"  ! skipped unparseable row: {row[:5]}")
                continue
            key = "|".join(str(p) for p in signature(
                card_label, txn_date, str(merchant), float(amount)))
            if s.scalar(select(Transaction).where(
                    Transaction.user_id == user.id,
                    Transaction.dedupe_key == key)) is not None:
                skipped += 1
                continue
            s.add(Transaction(
                user_id=user.id, card_id=card.id, tx_type=str(tx_type).lower(),
                merchant=str(merchant), txn_date=txn_date,
                original_amount=float(original), currency=str(currency),
                fx_rate_used=cfg.usd_to_dop if currency == "US$" else 1.0,
                amount_dop=float(amount),
                category_id=cats[category_name].id if category_name in cats else None,
                dedupe_key=key))
            imported += 1
        s.commit()
        print(f"Imported {imported} transaction(s), {skipped} already present.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.toml")
    migrate(p.parse_args().config)
