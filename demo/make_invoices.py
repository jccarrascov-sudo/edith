"""Generate demo/data/invoices.xlsx: 30 fictitious invoices (invented names, invented IDs).

Tax IDs start with 99, a prefix not used by real Peruvian RUC numbers, so no ID can
belong to a real company. Deterministic (fixed seed) so every reset yields the same data.
"""

from __future__ import annotations

import random
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
    return rows


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
        ws.cell(r, 3).number_format = "DD/MM/YYYY"
        ws.cell(r, 4).number_format = "#,##0.00"
    for col, width in zip("ABCDE", (28, 14, 12, 12, 34)):
        ws.column_dimensions[col].width = width
    about = wb.create_sheet("About")
    about.append(["All names, tax IDs and amounts in this file are fictitious, generated for a demo."])
    wb.save(path)
    return path


if __name__ == "__main__":
    print(f"Wrote {write()} (30 fictitious invoices)")
