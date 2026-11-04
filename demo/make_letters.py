"""The Excel -> Word demo: a payment reminder letter per client of invoices.xlsx.

`python demo/make_letters.py [folder]` writes, in the folder (default demo/data/letters):
  Payment reminder.docx          the template the user opens (blanks in capitals)
  invoices.xlsx                  a copy of the 30 fictitious invoices
and, with --hand, also the letter the user makes by hand for the first client
  Payment reminder - Paper Moth Bakery.docx
(the demo-check uses it as the user's demonstration; in the video the user types it).
`hand_letter(row, path)` is also how the demo-check builds the expected letter of any row."""

from __future__ import annotations

import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import docx
from docx.shared import Pt

HERE = Path(__file__).resolve().parent
TEMPLATE = "Payment reminder.docx"


def amount(v) -> str:
    if isinstance(v, str):
        v = float(v.replace(",", ""))
    return f"{float(v):,.2f}"


def when(v) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%d/%m/%Y")
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return s


def _letter(path: Path, client: str, tax: str, day: str, total: str, what: str) -> None:
    d = docx.Document()
    style = d.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    t = d.add_paragraph()
    r = t.add_run("Payment reminder")
    r.bold = True
    r.font.size = Pt(16)
    p = d.add_paragraph("Dear ")
    c = p.add_run(client)
    c.bold = True
    p.add_run(",")
    d.add_paragraph(f"This is a friendly reminder that the invoice of {day} for USD {total} "
                    f"({what}) is still pending.")
    d.add_paragraph(f"Tax ID on file: {tax}")
    d.add_paragraph("If you have already paid, please ignore this message.")
    d.add_paragraph("Best regards,")
    d.add_paragraph("Northwind Studio")
    d.save(str(path))


def template(path: Path) -> None:
    _letter(path, "CLIENT", "TAX ID", "DATE", "AMOUNT", "DESCRIPTION")


def hand_letter(row: dict, folder: Path) -> Path:
    """The letter as the user writes it by hand for `row`."""
    client = str(row["Client"]).strip()
    path = folder / f"Payment reminder - {client}.docx"
    _letter(path, client, str(row["Tax ID"] or "").strip(), when(row["Date"]) if row["Date"] else "",
            amount(row["Amount"]) if row["Amount"] not in (None, "") else "", str(row["Description"] or "").strip())
    return path


def make(folder: Path, hand: bool = False) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    sheet = folder / "invoices.xlsx"
    shutil.copy2(HERE / "data" / "invoices.xlsx", sheet)
    tpl = folder / TEMPLATE
    template(tpl)
    out = {"sheet": sheet, "template": tpl}
    if hand:
        sys.path.insert(0, str(HERE.parent / "src"))
        from edith.recipes.docs import sheet_rows

        _h, rows = sheet_rows(sheet)
        out["result"] = hand_letter(rows[0], folder)
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    made = make(Path(args[0]) if args else HERE / "data" / "letters", hand="--hand" in sys.argv)
    for k, v in made.items():
        print(f"{k}: {v}")
