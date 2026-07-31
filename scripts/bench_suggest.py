"""Score category-suggestion models against your own transactions.

Run this before changing BUDGET_OLLAMA_MODEL — especially when moving to a
bigger box. Twice now this project has picked a model by measuring, thrown the
harness away, and had to rebuild it; this is that harness, kept.

**Bigger is not reliably better here.** gemma4:e2b (7.2 GB) lost to qwen2.5:3b
(1.9 GB) because it never abstained, guessing "Mantenimiento" for every unknown
merchant. Most transactions the sweep sees are ones it *should* decline —
bank-transfer person names, marketplaces, unknown businesses — and a larger
model is not automatically better at declining. That is the axis to score on,
not size, which is why the summary below counts wrong answers and invented
answers separately instead of collapsing everything into one accuracy number.

Ground truth comes from your live database: every transaction you have already
filed under a real category. That is real personal data, so it is read at
runtime and never committed — this file contains no merchant names, and the
repo is public.

Because the DB only supplies merchants that *have* an answer, it cannot test
abstention on its own. Add should-abstain cases (and any corrections) in a
git-ignored JSON file, default `bench-cases.local.json`:

    {"SUPERMERCADO NACIONAL": "Supermercado", "INVERSIONES DELTA SRL": null}

(Illustrative names only — put your real ones in the file, not in this repo.)

Usage:
    PYTHONPATH=core .venv-web/bin/python scripts/bench_suggest.py \\
        --model qwen2.5:3b@http://100.123.55.111:11434 \\
        --model gemma4:cloud@http://127.0.0.1:11434

A bare --model NAME uses BUDGET_OLLAMA_URL. Read-only: never writes a
suggestion, never touches the DB.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "core"))  # budgetcore lives in core/

from sqlalchemy import select  # noqa: E402

from budgetcore.categorize import UNCATEGORIZED  # noqa: E402
from budgetcore.models import TxType  # noqa: E402
from web.app.db import get_sessionmaker  # noqa: E402
from web.app.models import Category, Transaction, User  # noqa: E402
from web.app.services.suggest import suggest_category  # noqa: E402
from web.app.settings import get_settings  # noqa: E402

DEFAULT_CASES = "bench-cases.local.json"


def _parse_model(spec: str, default_url: str) -> tuple[str, str]:
    """"name@url" -> (name, url); a bare "name" uses the configured URL."""
    name, _, url = spec.partition("@")
    return name, (url or default_url).rstrip("/")


def _ground_truth(session, email: str | None) -> tuple[str, list[str], dict]:
    """(user label, that user's categories, {merchant: expected_category}).

    Picks the account with the most filed transactions unless --email says
    otherwise, so the scoring reflects a real spending history.
    """
    users = list(session.execute(select(User.id, User.email)))
    if not users:
        raise SystemExit("No users in the database.")

    def filed(uid):
        return list(session.execute(
            select(Transaction.merchant, Category.name)
            .join(Category, Transaction.category_id == Category.id)
            .where(Transaction.user_id == uid,
                   Category.is_system.is_(False),
                   Category.name != UNCATEGORIZED,
                   Transaction.tx_type != TxType.RETIRO.value)
            .order_by(Transaction.created_at.desc())))

    if email:
        match = [(u, e) for u, e in users if e.lower() == email.lower()]
        if not match:
            raise SystemExit(f"No user with email {email!r}.")
        uid, label = match[0]
    else:
        uid, label = max(users, key=lambda ue: len(filed(ue[0])))

    truth: dict[str, str | None] = {}
    seen = set()
    for merchant, category in filed(uid):
        key = (merchant or "").strip()
        if key and key.upper() not in seen:
            seen.add(key.upper())
            truth[key] = category

    categories = [c.name for c in session.scalars(
        select(Category).where(Category.user_id == uid,
                               Category.is_system.is_(False))
        .order_by(Category.sort_order)).all()]
    return label, categories, truth


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", required=True, metavar="NAME[@URL]",
                        help="repeatable; compares in the order given")
    parser.add_argument("--email", help="account to score against "
                                        "(default: the one with most transactions)")
    parser.add_argument("--cases", default=DEFAULT_CASES,
                        help=f"extra/override cases JSON (default: {DEFAULT_CASES})")
    args = parser.parse_args()

    default_url = get_settings().ollama_url
    models = [_parse_model(m, default_url) for m in args.model]
    if any(not url for _, url in models):
        raise SystemExit("No URL: pass NAME@URL or set BUDGET_OLLAMA_URL.")

    with get_sessionmaker()() as session:
        label, categories, truth = _ground_truth(session, args.email)
    if not categories:
        raise SystemExit(f"{label} has no non-system categories.")

    # Local cases override the DB, and are the only source of abstain cases.
    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = _ROOT / cases_path
    if cases_path.exists():
        extra = json.loads(cases_path.read_text(encoding="utf-8"))
        unknown = {v for v in extra.values() if v is not None} - set(categories)
        if unknown:
            raise SystemExit(f"{cases_path.name}: not {label}'s categories: "
                             f"{', '.join(sorted(unknown))}")
        truth.update(extra)
        print(f"loaded {len(extra)} case(s) from {cases_path.name}")

    if not truth:
        raise SystemExit("No ground truth: file some transactions first, "
                         f"or create {cases_path.name}.")

    n = len(truth)
    abstain = sum(1 for v in truth.values() if v is None)
    print(f"account {label} — {n} merchants ({n - abstain} with a category, "
          f"{abstain} should-abstain), {len(categories)} categories\n")

    results = {}
    for name, url in models:
        answers, ok, wrong, invented, over = {}, 0, 0, 0, 0
        elapsed = 0.0
        for merchant, expected in truth.items():
            started = time.time()
            got = suggest_category(merchant, categories, url=url, model=name)
            elapsed += time.time() - started
            answers[merchant] = got
            if got == expected:
                ok += 1
            elif expected is None:
                invented += 1   # answered where it should have declined
            elif got is None:
                over += 1       # declined where an answer was knowable
            else:
                wrong += 1      # picked the wrong category
        results[name] = answers
        print(f"{name:24} {ok:3}/{n}  wrong={wrong}  invented={invented}  "
              f"over-abstained={over}  {elapsed / n:.1f}s/call")

    print("\n--- per-merchant  (= right, X wrong, ! invented, . over-abstained) ---")
    print(f"{'merchant':34} {'expected':16} " + " ".join(f"{n_:20}" for n_, _ in models))
    for merchant, expected in truth.items():
        row = f"{merchant[:32]:34} {str(expected)[:15]:16} "
        for name, _ in models:
            got = results[name][merchant]
            if got == expected:
                mark = "="
            elif expected is None:
                mark = "!"
            elif got is None:
                mark = "."
            else:
                mark = "X"
            row += f"{mark} {str(got)[:18]:18} "
        print(row)


if __name__ == "__main__":
    main()
