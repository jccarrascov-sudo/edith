"""Reading the files of a recipe (Excel, Word) and comparing two Word documents.

The comparison is what "the same result as the user's" means for a document: the same
paragraphs and table cells (spacing ignored), the same bold and italic words, and the same
file name. It is also the source of the preview's highlights."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


# --- Excel -------------------------------------------------------------------------------------

def sheet_rows(path: str | Path) -> tuple[list[str], list[dict]]:
    """Header and rows (as {header: value}) of the first sheet; empty rows skipped."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else f"col{i + 1}" for i, h in enumerate(next(it, []) or [])]
        rows = []
        for values in it:
            if values is None or all(v is None or str(v).strip() == "" for v in values):
                continue
            rows.append({h: v for h, v in zip(header, values)})
        return header, rows
    finally:
        wb.close()


def show_value(v) -> str:
    from datetime import date, datetime

    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.isoformat(" ")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def sheet_brief(path: str | Path, highlight: dict | None = None, limit: int = 12) -> str:
    header, rows = sheet_rows(path)
    lines = [f"Sheet {Path(path).name}: {len(rows)} rows. Columns: {header}",
             "Values as Python reads them with openpyxl (data_only=True): type and repr:"]
    for i, r in enumerate(rows[:limit]):
        mark = "  <- the row the user used" if highlight is not None and r == highlight else ""
        lines.append(f"row {i + 2}: " + "; ".join(f"{k}={type(v).__name__}:{v!r}" for k, v in r.items()) + mark)
    if highlight is not None and highlight not in rows[:limit]:
        i = rows.index(highlight)
        lines.append(f"row {i + 2}: " + "; ".join(f"{k}={type(v).__name__}:{v!r}" for k, v in highlight.items())
                     + "  <- the row the user used")
    return "\n".join(lines)


# --- Word --------------------------------------------------------------------------------------

@dataclass
class DocShape:
    paragraphs: list[str] = field(default_factory=list)
    cells: list[str] = field(default_factory=list)
    bold: list[str] = field(default_factory=list)
    italic: list[str] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(self.paragraphs + self.cells)


def _styled(paragraph, attr: str) -> list[str]:
    """Runs of text with the attribute (adjacent runs merged), from run or style formatting."""
    out, cur = [], ""
    for run in paragraph.runs:
        on = getattr(run, attr)
        if on is None and paragraph.style is not None and paragraph.style.font is not None:
            on = getattr(paragraph.style.font, attr)
        if on:
            cur += run.text
        elif cur:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return [squash(s) for s in out if squash(s)]


def shape(path: str | Path) -> DocShape:
    import docx

    d = docx.Document(str(path))
    s = DocShape()
    for p in d.paragraphs:
        if squash(p.text):
            s.paragraphs.append(squash(p.text))
            s.bold += _styled(p, "bold")
            s.italic += _styled(p, "italic")
    for t in d.tables:
        for row in t.rows:
            for c in row.cells:
                if squash(c.text):
                    s.cells.append(squash(c.text))
                for p in c.paragraphs:
                    s.bold += _styled(p, "bold")
    return s


def doc_brief(path: str | Path) -> str:
    """The document as the model sees it: paragraphs, table cells, bold and italic words."""
    s = shape(path)
    lines = [f"Document {Path(path).name}:"]
    lines += [f"  p{i + 1}: {t}" for i, t in enumerate(s.paragraphs)]
    if s.cells:
        lines.append("  table cells: " + " | ".join(s.cells))
    if s.bold:
        lines.append(f"  bold: {s.bold}")
    if s.italic:
        lines.append(f"  italic: {s.italic}")
    return "\n".join(lines)


def same_name(a: str | Path, b: str | Path) -> bool:
    return fold(squash(Path(a).name)) == fold(squash(Path(b).name))


def compare(expected: str | Path, got: str | Path, check_name: bool = True) -> list[str]:
    """The differences between the user's document and the program's ([] = the same)."""
    e, g = shape(expected), shape(got)
    out = []
    if check_name and not same_name(expected, got):
        out.append(f'file name: expected "{Path(expected).name}", got "{Path(got).name}"')
    for what, a, b in (("paragraph", e.paragraphs, g.paragraphs), ("table cell", e.cells, g.cells)):
        for i in range(max(len(a), len(b))):
            x = a[i] if i < len(a) else "(nothing)"
            y = b[i] if i < len(b) else "(nothing)"
            if x != y:
                out.append(f'{what} {i + 1}: expected "{x}", got "{y}"')
                if len(out) >= 6:
                    return out
    if e.bold != g.bold:
        out.append(f"bold text: expected {e.bold}, got {g.bold}")
    if e.italic != g.italic:
        out.append(f"italic text: expected {e.italic}, got {g.italic}")
    return out


def similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b, autojunk=False).ratio()
