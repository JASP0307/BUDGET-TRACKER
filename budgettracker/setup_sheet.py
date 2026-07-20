"""One-time helper: create the budget spreadsheet and wire its ID into config.

Run once after OAuth succeeds:  python -m budgettracker.setup_sheet
Creates a spreadsheet with the Transacciones + Presupuesto tabs, writes the
transaction header and the budget table (with live month-to-date formulas),
then replaces the placeholder spreadsheet_id in config.toml.
"""

from __future__ import annotations

import re
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from . import config as config_mod
from .sheets import SCOPES, TX_HEADER


def _gastado_formula(tab: str, row: int) -> str:
    """Month-to-date spend for the category in column A of `row`.

    Dates are stored as ISO text, so LEFT(date, 7) == 'YYYY-MM' selects the
    current month; column F is category, column G is the signed amount.
    """
    rng = f"'{tab}'!"
    return (
        f"=SUMPRODUCT(({rng}$F$2:$F$2000=$A{row})"
        f"*(LEFT({rng}$B$2:$B$2000,7)=TEXT(TODAY(),\"YYYY-MM\"))"
        f"*{rng}$G$2:$G$2000)"
    )


def build_sheet(config_path: str = "config.toml") -> str:
    cfg = config_mod.load(config_path)
    creds = Credentials.from_authorized_user_file(cfg.gmail_token_file, SCOPES)
    service = build("sheets", "v4", credentials=creds)

    created = service.spreadsheets().create(body={
        "properties": {"title": "Presupuesto Mensual — Budget Tracker"},
        "sheets": [
            {"properties": {"title": cfg.transactions_tab}},
            {"properties": {"title": cfg.budget_tab}},
        ],
    }).execute()
    sid = created["spreadsheetId"]
    url = created["spreadsheetUrl"]

    # Transaction log header.
    service.spreadsheets().values().update(
        spreadsheetId=sid, range=f"{cfg.transactions_tab}!A1",
        valueInputOption="USER_ENTERED", body={"values": [TX_HEADER]},
    ).execute()

    # Budget table: Categoría | Presupuesto | Gastado (mes) | Restante.
    rows = [["Categoría", "Presupuesto (RD$)", "Gastado (mes)", "Restante"]]
    for i, (cat, amount) in enumerate(cfg.budget.items()):
        r = i + 2
        rows.append([cat, amount, _gastado_formula(cfg.transactions_tab, r),
                     f"=B{r}-C{r}"])
    total = len(rows)  # header + categories; totals go on the next row
    rows.append(["TOTAL", f"=SUM(B2:B{total})", f"=SUM(C2:C{total})",
                 f"=SUM(D2:D{total})"])
    service.spreadsheets().values().update(
        spreadsheetId=sid, range=f"{cfg.budget_tab}!A1",
        valueInputOption="USER_ENTERED", body={"values": rows},
    ).execute()

    # Patch config.toml with the real id.
    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'spreadsheet_id = ".*"', f'spreadsheet_id = "{sid}"', text)
    path.write_text(text, encoding="utf-8")

    print(f"Created spreadsheet: {url}")
    print(f"spreadsheet_id written to {config_path}: {sid}")
    return sid


if __name__ == "__main__":
    build_sheet()
