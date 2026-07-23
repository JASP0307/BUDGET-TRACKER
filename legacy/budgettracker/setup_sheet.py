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

    Google stores the ISO dates in column B as real dates, so we bound by the
    current month with SUMIFS; column F is category, column G the signed amount.
    """
    return (
        f"=SUMIFS({tab}!$G$2:$G,{tab}!$F$2:$F,$A{row},"
        f'{tab}!$B$2:$B,">="&DATE(YEAR(TODAY()),MONTH(TODAY()),1),'
        f'{tab}!$B$2:$B,"<"&(EOMONTH(TODAY(),0)+1))'
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
    # Header + the plain (Categoría, Presupuesto) columns go in one block write.
    cats = list(cfg.budget.items())
    n = len(cats)
    last_cat_row = n + 1          # categories occupy rows 2..n+1
    total_row = n + 2
    block = [["Categoría", "Presupuesto (RD$)", "Gastado (mes)", "Restante"]]
    block += [[cat, amount] for cat, amount in cats]
    service.spreadsheets().values().update(
        spreadsheetId=sid, range=f"{cfg.budget_tab}!A1",
        valueInputOption="USER_ENTERED", body={"values": block},
    ).execute()

    # Formula cells are written individually: a big A1-anchored array write makes
    # Google shift even absolute references, but single-cell writes preserve them.
    data = []
    for i in range(n):
        r = i + 2
        data.append({"range": f"{cfg.budget_tab}!C{r}",
                     "values": [[_gastado_formula(cfg.transactions_tab, r)]]})
        data.append({"range": f"{cfg.budget_tab}!D{r}", "values": [[f"=B{r}-C{r}"]]})
    data.append({"range": f"{cfg.budget_tab}!A{total_row}", "values": [["TOTAL"]]})
    for col in ("B", "C", "D"):
        data.append({"range": f"{cfg.budget_tab}!{col}{total_row}",
                     "values": [[f"=SUM({col}2:{col}{last_cat_row})"]]})
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=sid,
        body={"valueInputOption": "USER_ENTERED", "data": data},
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
