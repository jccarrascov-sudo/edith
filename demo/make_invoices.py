"""Generate demo/data/invoices.xlsx: 30 fictitious invoices (invented names, invented IDs).

Tax IDs start with 99, a prefix not used by real Peruvian RUC numbers, so no ID can
belong to a real company. Deterministic (fixed seed) so every reset yields the same data.

Hard cases, on purpose (row 1 stays a plain one, it is the usual demonstration):
- accents and ñ in client names and descriptions;
- amounts stored as TEXT with thousands commas ("1,234.50"), and whole-number amounts;
- dates in two formats: real Excel dates (shown dd/mm/yyyy) and ISO text ("2026-10-03");
- one empty amount cell, which a machine must report without breaking the run.
"""

from __future__ import annotations

import random
import time
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

OUT = Path(__file__).resolve().parent / "data" / "invoices.xlsx"
HEADERS = ["Client", "Tax ID", "Date", "Amount", "Description"]

FIRST = ["Blue Heron", "Copper Kettle", "Quiet Lantern", "Paper Moth", "Seven Pines", "Little Anchor",
         "Amber Field", "Tin Robot", "Velvet Cactus", "North Window", "Silver Fern", "Round Pebble",
         "Lazy Comet", "Golden Spoon", "Brave Turtle"]
SECOND = ["Bakery", "Supplies", "Studio", "Workshop", "Traders", "Print House", "Garden Co.",
          "Repairs", "Coffee Bar", "Books"]
ITEMS = ["Office paper (10 reams)", "Monthly cleaning service", "Printer toner x2", "Website maintenance",
         "Catering for team meeting", "Ergonomic chairs x3", "Delivery service, October",
         "Coffee beans 5 kg", "Logo redesign", "Network cable installation", "Plant care, monthly",
         "Notebooks and pens", "Laptop repair", "Event photography", "Shelf assembly"]


ACCENTED_CLIENTS = {4: "Panadería Ñandú", 8: "Café Señorial", 12: "Librería Acuña",
                    16: "Lavandería Peñón", 21: "Taller Ñuñoa", 26: "Estudio Andrés Muñoz"}
ACCENTED_ITEMS = {4: "Pan de maíz y empanadas", 12: "Diseño de logotipo", 21: "Reparación de máquina"}
TEXT_AMOUNTS = (5, 11, 19, 27)    # amount typed as text with a thousands comma
WHOLE_AMOUNTS = (9, 23)           # amount without decimals
ISO_TEXT_DATES = (6, 14, 20, 28)  # date typed as text yyyy-mm-dd
EMPTY_AMOUNT = 17                 # one missing value


def generate(seed: int = 2026) -> list[list]:
    rng = random.Random(seed)
    names = rng.sample([f"{a} {b}" for a in FIRST for b in SECOND], 30)
    start = date(2026, 9, 1)
    rows = []
    for name in names:
        tax_id = "99" + "".join(str(rng.randint(0, 9)) for _ in range(9))
        day = start + timedelta(days=rng.randint(0, 55))
        amount = round(rng.uniform(80, 4800), 2)
        rows.append([name, tax_id, day, amount, rng.choice(ITEMS)])
    for i, name in ACCENTED_CLIENTS.items():
        rows[i][0] = name
    for i, item in ACCENTED_ITEMS.items():
        rows[i][4] = item
    for i in WHOLE_AMOUNTS:
        rows[i][3] = float(round(rows[i][3]))
    for i in TEXT_AMOUNTS:
        rows[i][3] = f"{rows[i][3] + 1000:,.2f}"   # >= 1,000 so the comma is always there
    for i in ISO_TEXT_DATES:
        rows[i][2] = rows[i][2].isoformat()
    rows[EMPTY_AMOUNT][3] = None
    return rows


def close_in_excel(path: Path) -> bool:
    """If Excel has this workbook open (it locks the file), close it without saving."""
    try:
        import comtypes.client

        excel = comtypes.client.GetActiveObject("Excel.Application")
    except Exception:
        return False
    for wb in list(excel.Workbooks):
        if Path(str(wb.FullName)).resolve() == path.resolve():
            wb.Close(False)
            return True
    return False


def write(path: Path = OUT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2F6FDE")
    for row in generate():
        ws.append(row)
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 2).number_format = "@"           # keep tax IDs as text
        if not isinstance(ws.cell(r, 3).value, str):
            ws.cell(r, 3).number_format = "DD/MM/YYYY"
        if not isinstance(ws.cell(r, 4).value, str):
            ws.cell(r, 4).number_format = "#,##0.00"
    for col, width in zip("ABCDE", (28, 14, 12, 12, 34)):
        ws.column_dimensions[col].width = width
    about = wb.create_sheet("About")
    about.append(["All names, tax IDs and amounts in this file are fictitious, generated for a demo."])
    try:
        wb.save(path)
    except PermissionError:
        if not close_in_excel(path):
            raise
        time.sleep(0.5)
        wb.save(path)
    return path


if __name__ == "__main__":
    print(f"Wrote {write()} (30 fictitious invoices)")
