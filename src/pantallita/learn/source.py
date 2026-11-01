"""The data the user copied from: locate the spreadsheet and find the demonstrated row.

The row is found deterministically: every value the user typed is compared with every
cell, through the ways a cell can be shown (Excel display format, plain number, dates in
common formats). The row matching most fields is the demonstration; its columns give the
field -> column mapping the plan must reproduce.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..config import ROOT

log = logging.getLogger(__name__)

SHEET_EXT = (".xlsx", ".xlsm")
_INVISIBLE = re.compile("[​-‏‪-‮⁦-⁩﻿]")
DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y")


@dataclass
class Sheet:
    path: Path
    name: str
    headers: list[str]
    rows: list[dict]           # header -> raw cell value
    formats: dict[str, str]    # header -> Excel number format of the first data row

    def display(self, row: dict, header: str) -> str:
        return excel_display(row.get(header), self.formats.get(header, "General"))


def excel_display(value, number_format: str = "General") -> str:
    """Roughly what Excel shows for a cell (what a user would copy)."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        fmt = number_format.upper()
        if fmt.startswith("DD/MM/YYYY"):
            return value.strftime("%d/%m/%Y")
        if fmt.startswith("MM/DD/YYYY") or fmt.startswith("M/D/YYYY"):
            return value.strftime("%m/%d/%Y")
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "#,##0.00" in number_format:
            return f"{value:,.2f}"
        if "0.00" in number_format:
            return f"{value:.2f}"
        return str(int(value)) if float(value).is_integer() else str(value)
    return str(value)


def cell_texts(value, number_format: str = "General") -> set[str]:
    """Every reasonable way a cell value could have been typed into a form."""
    if value is None:
        return set()
    out = {excel_display(value, number_format)}
    if isinstance(value, (datetime, date)):
        out |= {value.strftime(f) for f in DATE_FORMATS}
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out |= {str(value), f"{value:.2f}", f"{value:,.2f}"}
        if float(value).is_integer():
            out.add(str(int(value)))
    else:
        out.add(str(value).strip())
    return {norm(t) for t in out}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def load_sheet(path: Path, sheet: str | None = None) -> Sheet:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=False)
    ws = wb[sheet] if sheet else wb.active
    rows_iter = ws.iter_rows()
    header_cells = next(rows_iter)
    headers = [str(c.value).strip() if c.value is not None else f"col{c.column}" for c in header_cells]
    rows, formats = [], {}
    for cells in rows_iter:
        values = {h: c.value for h, c in zip(headers, cells)}
        if all(v is None for v in values.values()):
            continue
        if not formats:
            formats = {h: c.number_format for h, c in zip(headers, cells)}
        rows.append(values)
    return Sheet(path, ws.title, headers, rows, formats)


@dataclass
class RowMatch:
    index: int                      # 0-based data row
    columns: dict[str, str | None]  # form field name -> sheet header (None = no cell matched)

    @property
    def matched(self) -> int:
        return sum(c is not None for c in self.columns.values())


def match_row(sheet: Sheet, typed: dict[str, str]) -> RowMatch | None:
    """The data row that best explains the typed values (needs at least half of the fields)."""
    best: RowMatch | None = None
    for i, row in enumerate(sheet.rows):
        columns = {}
        for name, value in typed.items():
            columns[name] = next((h for h in sheet.headers
                                  if norm(value) in cell_texts(row.get(h), sheet.formats.get(h, "General"))), None)
        candidate = RowMatch(i, columns)
        if best is None or candidate.matched > best.matched:
            best = candidate
    if best is None or best.matched * 2 < len(typed):
        return None
    return best


def open_workbooks() -> list[Path]:
    """Workbooks currently open in Excel (via COM); empty if Excel is not running."""
    try:
        import comtypes.client

        excel = comtypes.client.GetActiveObject("Excel.Application")
        return [Path(wb.FullName) for wb in excel.Workbooks if str(wb.FullName).lower().endswith(SHEET_EXT)]
    except Exception:
        return []


def find_workbook(window_titles: list[str], extra_dirs: list[Path] | None = None) -> Path | None:
    """The spreadsheet seen in the demonstration's windows: Excel's open workbooks first, then
    files with that name in the usual folders."""
    # Excel titles look like "invoices.xlsx - Excel" (or "... - Saved - Excel").
    # With "hide extensions" on (the Windows default) the title is just "invoices - Excel".
    stems = set()
    for title in window_titles:
        title = _INVISIBLE.sub("", title)  # Excel wraps the name in bidi marks: "‪invoices‬"
        parts = [p.strip() for p in title.split(" - ")]
        if len(parts) >= 2 and parts[-1].lower() == "excel":
            stems.add(Path(parts[0]).stem.lower() if parts[0].lower().endswith(SHEET_EXT) else parts[0].lower())
    if not stems:
        return None
    for path in open_workbooks():
        if path.stem.lower() in stems:
            return path
    home = Path.home()
    dirs = (extra_dirs or []) + [ROOT / "demo" / "data", home / "Documents", home / "Downloads",
                                 home / "Desktop", home / "OneDrive" / "Documents", home / "OneDrive"]
    for d in dirs:
        for stem in stems:
            for ext in SHEET_EXT:
                if (d / f"{stem}{ext}").exists():
                    return d / f"{stem}{ext}"
    return None
