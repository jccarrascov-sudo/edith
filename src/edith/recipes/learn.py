"""Learning a recipe from what the user just did by hand.

1. Find the demonstration: the Word document the user just saved, the template it came from
   (the most similar older document they opened) and the Excel sheet they used, from Windows'
   recent files; the row they used and the column that tells rows apart are found with no model.
2. Compile: Muse Spark writes a program for level 1 (direct data: openpyxl + python-docx); if it
   can't pass, for level 2 (Word through COM, invisible).
3. Verify (mandatory): run it on COPIES with the user's own values and compare with the user's
   document; then on two more rows (their values must appear, the demo's must not). On failure
   Muse Spark gets the differences and corrects, at most 2 times per level, then the next level.
Nothing enters the recipe memory without passing.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from . import docs, sandbox
from .store import Recipe, RecipeStore

log = logging.getLogger(__name__)

LEVELS = (1, 2)            # levels 3-5 come in later stages
CORRECTIONS = 2            # per level


class CannotLearn(Exception):
    """Why there is no recipe, in one plain line for the panel (the key names a TEXT entry)."""

    def __init__(self, key: str, **kw):
        super().__init__(key)
        self.key, self.kw = key, kw


@dataclass
class Demo:
    sheet: Path
    template: Path
    result: Path
    manual_s: float = 0.0


# --- 1. the demonstration ---------------------------------------------------------------------

def recent_files(minutes: float) -> list[tuple[Path, float]]:
    """(target, opened at) of Windows' recent items (shortcuts in %APPDATA%\\...\\Recent)."""
    folder = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    since = time.time() - minutes * 60
    out = []
    try:
        links = [p for p in folder.glob("*.lnk") if p.stat().st_mtime >= since]
    except OSError:
        return out
    if not links:
        return out
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    shell = win32com.client.Dispatch("WScript.Shell")
    for link in links:
        try:
            target = Path(shell.CreateShortcut(str(link)).TargetPath)
        except Exception:
            continue
        if target.suffix.lower() in (".docx", ".xlsx") and target.is_file():
            out.append((target, link.stat().st_mtime))
    return out


def find_demo(minutes: float = 30, recent: list[tuple[Path, float]] | None = None) -> Demo:
    recent = recent_files(minutes) if recent is None else recent
    since = time.time() - minutes * 60
    words = [(p, t) for p, t in recent if p.suffix.lower() == ".docx" and not p.name.startswith("~$")]
    sheets = [(p, t) for p, t in recent if p.suffix.lower() == ".xlsx" and not p.name.startswith("~$")]
    saved = [p for p, _t in words if p.stat().st_mtime >= since]
    if not saved:
        raise CannotLearn("learn_no_doc", minutes=int(minutes))
    result = max(saved, key=lambda p: p.stat().st_mtime)
    # the template: an older document (opened recently, or next to the result) most like the result
    candidates = {p for p, _t in words if p != result}
    candidates |= {p for p in result.parent.glob("*.docx") if p != result and not p.name.startswith("~$")}
    text = docs.shape(result).text()
    scored = []
    for c in candidates:
        try:
            if c.stat().st_mtime > result.stat().st_mtime:
                continue
            scored.append((docs.similarity(docs.shape(c).text(), text), c))
        except Exception:
            continue
    scored = [s for s in scored if s[0] >= 0.35]
    if not scored:
        raise CannotLearn("learn_no_template", doc=result.name)
    template = max(scored)[1]
    if not sheets:
        near = sorted(result.parent.glob("*.xlsx"), key=lambda p: -p.stat().st_mtime)
        sheets = [(p, p.stat().st_mtime) for p in near if not p.name.startswith("~$")]
    if not sheets:
        raise CannotLearn("learn_no_sheet")
    sheet = max(sheets, key=lambda s: s[1])[0]
    opened = [t for p, t in words if p == template]
    manual = max(0.0, result.stat().st_mtime - opened[0]) if opened else 0.0
    return Demo(sheet, template, result, manual if manual < 3600 else 0.0)


def value_forms(v) -> set[str]:
    """How a cell's value may look once typed in a document."""
    if v is None or str(v).strip() == "":
        return set()
    if isinstance(v, (datetime, date)):
        d = v.date() if isinstance(v, datetime) else v
        return {d.strftime("%d/%m/%Y"), d.strftime("%Y-%m-%d"), f"{d.day}/{d.month}/{d.year}", d.strftime("%d-%m-%Y")}
    if isinstance(v, bool):
        return {str(v)}
    if isinstance(v, (int, float)):
        f = float(v)
        forms = {f"{f:,.2f}", f"{f:.2f}", f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                 f"{f:.2f}".replace(".", ",")}
        if f.is_integer():
            forms |= {str(int(f)), f"{int(f):,}"}
        return forms
    return {docs.squash(str(v))}


def demo_row(sheet: Path, result: Path) -> tuple[list[str], list[dict], dict, str]:
    """(header, rows, the row the user used, the key column), without a model: the row with
    the most values in the user's document; the key = a text column whose value is in the
    document and unique in the sheet (names before codes)."""
    header, rows = docs.sheet_rows(sheet)
    text = docs.fold(docs.shape(result).text())
    best, best_n = None, 0
    for r in rows:
        n = sum(1 for v in r.values() for f in value_forms(v) if f and docs.fold(f) in text)
        if n > best_n:
            best, best_n = r, n
    if best is None:
        raise CannotLearn("learn_no_row", sheet=sheet.name)
    keys = []
    for pos, col in enumerate(header):
        v = best.get(col)
        if not isinstance(v, str) or len(v.strip()) < 2 or docs.fold(docs.squash(v)) not in text:
            continue
        values = [docs.fold(docs.squash(str(r.get(col) or ""))) for r in rows]
        filled = [x for x in values if x]
        unique_everywhere = len(set(filled)) == len(filled)
        if values.count(docs.fold(docs.squash(v))) == 1:
            keys.append((unique_everywhere, -pos, col))      # an identifier column, the leftmost
    if not keys:
        raise CannotLearn("learn_no_key", sheet=sheet.name)
    return header, rows, best, max(keys)[2]


# --- 2. the program ---------------------------------------------------------------------------

PROMPT = {1: """You turn a task a person did once by hand into a Python program that redoes it for other rows.
The person opened the Word template, typed the values of ONE row of the Excel sheet into it and
saved it as a new document. Write the program that does exactly the same for any row.

Rules:
- Exactly one top-level `def run(sheet, template, key, out_dir):` (helpers may be nested inside).
  sheet = path of the .xlsx, template = path of the .docx template, key = the value of the column
  "{key_column}" that identifies the row, out_dir = the folder for the new document.
- Allowed imports only: openpyxl, docx (python-docx), and datetime, re, copy, decimal, math, string,
  unicodedata, collections, itertools, pathlib, calendar, textwrap, functools, operator. No os, no
  subprocess, no network, no eval/exec, no dunder names, no deleting or renaming files.
- Read the sheet with openpyxl.load_workbook(sheet, data_only=True); find the row whose
  "{key_column}" equals key (compare str(value).strip() case-insensitively); use the header row.
- Open the template with docx.Document(template); change only what the person changed; keep the
  formatting: replace text inside runs (a value may span several runs: rebuild the paragraph's
  runs keeping each run's formatting), and make bold/italic exactly what the person's document
  shows. Then save as a NEW file in out_dir named like the person named theirs, from the row's
  values; return its full path.
- Never write any value of the person's row as a literal: every value comes from the row.
- Other rows can differ: accents and ñ, amounts as numbers or as text with commas, dates as
  datetime or as text in another format, empty cells (leave them blank, never crash). Format
  numbers and dates exactly as the person did in their document.

Reply in this format only:
NAME: a short name for the task, in the person's language (e.g. "Recordatorio de pago")
STEPS:
- one step per line, plain words, in the person's language (3 to 6 steps)
```python
the program
```""",
          2: """You turn a task a person did once by hand into a Python program that redoes it for other rows,
driving Microsoft Word through COM (it is invisible; the person never sees it).
The person opened the Word template, typed the values of ONE row of the Excel sheet into it and
saved it as a new document. Write the program that does exactly the same for any row.

Rules:
- Exactly one top-level `def run(sheet, template, key, out_dir, word):` (helpers nested inside).
  word = a running Word.Application (Visible=False). sheet = path of the .xlsx, template = path of
  the .docx template, key = the value of the column "{key_column}" that identifies the row,
  out_dir = the folder for the new document.
- Allowed imports only: openpyxl and datetime, re, copy, decimal, math, string, unicodedata,
  collections, itertools, pathlib, calendar, textwrap, functools, operator. No os, no subprocess,
  no network, no eval/exec, no dunder names, no deleting files.
- Read the sheet with openpyxl.load_workbook(sheet, data_only=True); find the row whose
  "{key_column}" equals key (str(value).strip(), case-insensitive).
- doc = word.Documents.Open(template, ReadOnly=True); replace with Find so the formatting is kept.
  A Range SHRINKS to what Find found, so take a FRESH range for every replacement, and pass the
  arguments POSITIONALLY (this COM bridge silently ignores keyword arguments: with them Execute
  returns True and replaces nothing). The replacement text must be at most 255 characters:
      rng = doc.Content
      #                FindText MatchCase Whole Wildc Sounds AllForms Forward Wrap Format ReplaceWith Replace
      rng.Find.Execute(old,     True,     False, False, False, False,  True,   1,   False,  new,        2)
  Replace longer placeholders before shorter ones that they contain. Find/Replace keeps the
  found text's formatting; set Font.Bold/Italic only where the person's document differs.
  doc.SaveAs2(full path in out_dir, 16); doc.Close(0); return the path.
  Name the file like the person named theirs, from the row's values.
- Never write any value of the person's row as a literal: every value comes from the row.
- Other rows can differ: accents and ñ, amounts as numbers or text with commas, dates as datetime
  or text, empty cells (leave blank, never crash). Format numbers and dates as the person did.

Reply in this format only:
NAME: a short name for the task, in the person's language
STEPS:
- one step per line, plain words, in the person's language (3 to 6 steps)
```python
the program
```"""}


@dataclass
class Draft:
    name: str
    steps: list[str]
    program: str


def parse(reply: str) -> Draft | None:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reply or "", re.DOTALL)
    if not m:
        return None
    name = re.search(r"^\s*NAME:\s*(.+)$", reply, re.MULTILINE)
    steps_block = re.search(r"STEPS:\s*\n(.*?)```", reply, re.DOTALL)
    steps = [s.strip(" -*\t") for s in (steps_block.group(1).splitlines() if steps_block else []) if s.strip(" -*\t")]
    return Draft(name.group(1).strip() if name else "", steps[:8], m.group(1).strip() + "\n")


def literals_from_row(program: str, row: dict, template_text: str) -> list[str]:
    """Values of the demo row written into the program (not allowed: they must come from the row)."""
    folded_template = docs.fold(template_text)
    forms = {docs.fold(f) for v in row.values() for f in value_forms(v) if len(f) >= 3}
    forms = {f for f in forms if f not in folded_template}
    bad = []
    for node in ast.walk(ast.parse(program)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            c = docs.fold(docs.squash(node.value))
            bad += [f for f in forms if f and (c == f or (len(f) >= 5 and f in c))]
    return sorted(set(bad))


# --- 3. verification --------------------------------------------------------------------------

@dataclass
class Check:
    ok: bool
    problems: list[str]
    seconds: float = 0.0


def verify(program: str, level: int, demo: Demo, rows: list[dict], row: dict, key_column: str,
           work: Path) -> Check:
    """On copies: the user's row must give the user's document; two other rows must give their
    own values and none of the demo's."""
    try:
        sandbox.check(program, level)
    except sandbox.UnsafeProgram as exc:
        return Check(False, [f"the program breaks a rule: {exc}"])
    template_text = docs.shape(demo.template).text()
    bad = literals_from_row(program, row, template_text)
    if bad:
        return Check(False, [f"values of the row are written in the program as literals: {bad}; read them from the row"])
    inp = work / "in"
    inp.mkdir(parents=True, exist_ok=True)
    sheet = shutil.copy2(demo.sheet, inp / demo.sheet.name)
    template = shutil.copy2(demo.template, inp / demo.template.name)
    t0 = time.monotonic()
    out = sandbox.run(program, level, str(sheet), str(template), str(row[key_column]), str(work / "demo"))
    if not out.ok:
        where = f" (line {out.line})" if out.line else ""
        return Check(False, [f"it failed with the person's own row: {out.error}{where}"], out.seconds)
    problems = docs.compare(demo.result, out.out)
    if problems:
        return Check(False, ["the result differs from the person's document:"] + problems, out.seconds)
    demo_key = docs.fold(docs.squash(str(row[key_column])))
    others = [r for r in rows if r is not row and str(r.get(key_column) or "").strip()]
    picks = others[:1] + others[-1:] if len(others) > 1 else others
    for i, other in enumerate(picks):
        key = str(other[key_column]).strip()
        o = sandbox.run(program, level, str(sheet), str(template), key, str(work / f"row{i}"))
        if not o.ok:
            where = f" (line {o.line})" if o.line else ""
            return Check(False, [f'it failed with the row "{key}": {o.error}{where}'], time.monotonic() - t0)
        text = docs.fold(docs.shape(o.out).text())
        if docs.fold(docs.squash(key)) not in text:
            return Check(False, [f'with the row "{key}" its value does not appear in the document'], time.monotonic() - t0)
        if demo_key not in docs.fold(template_text) and demo_key not in docs.fold(key) and demo_key in text:
            return Check(False, [f'with the row "{key}" the document still says "{row[key_column]}"'], time.monotonic() - t0)
    return Check(True, [], time.monotonic() - t0)


# --- the whole thing --------------------------------------------------------------------------

def compile_demo(cfg, demo: Demo, ask: Callable[[str, str], str] | None = None,
                 on_status: Callable[[str, dict], None] = lambda key, kw: None,
                 levels: tuple[int, ...] = LEVELS, store: RecipeStore | None = None) -> Recipe:
    """Compile the demonstration into a verified recipe (saved) or raise CannotLearn."""
    store = store or RecipeStore(cfg)
    header, rows, row, key_column = demo_row(demo.sheet, demo.result)
    ask = ask or _muse(cfg)
    brief = "\n\n".join([docs.sheet_brief(demo.sheet, highlight=row), docs.doc_brief(demo.template),
                         "The person's result (match it exactly):\n" + docs.doc_brief(demo.result),
                         f'The person saved it as "{demo.result.name}". Key column: "{key_column}" '
                         f'(value in the person\'s row: {row[key_column]!r}).'])
    tried = []
    for level in levels:
        on_status("learn_compiling", {"level": level})
        conversation = brief
        reply = ask(PROMPT[level].format(key_column=key_column), conversation)
        for attempt in range(CORRECTIONS + 1):
            draft = parse(reply)
            work = Path(tempfile.mkdtemp(prefix=f"edith-recipe-l{level}-"))
            try:
                if draft is None:
                    check = Check(False, ["the reply had no ```python program```"])
                else:
                    on_status("learn_verifying", {"level": level})
                    check = verify(draft.program, level, demo, rows, row, key_column, work)
            finally:
                shutil.rmtree(work, ignore_errors=True)
            log.info("recipe level %d attempt %d: %s", level, attempt + 1, "verified" if check.ok else check.problems[:2])
            if check.ok:
                r = Recipe(id=RecipeStore.new_id(draft.name or demo.template.stem), name=draft.name or demo.template.stem,
                           kind="excel_to_word", level=level, steps=draft.steps, sheet=str(demo.sheet),
                           template=str(demo.template), result=str(demo.result), key_column=key_column,
                           demo_key=str(row[key_column]).strip(), out_dir=str(demo.result.parent),
                           verified={"level": level, "attempts": attempt + 1, "ts": time.time(),
                                     "rows": len(rows), "seconds": round(check.seconds, 2)},
                           manual_s=round(demo.manual_s, 1), created=time.time())
                store.save(r, draft.program)
                store.keep_demo(r)
                return r
            tried.append(f"level {level}: {check.problems[0]}")
            if attempt < CORRECTIONS:
                on_status("learn_correcting", {"level": level, "n": attempt + 1})
                conversation = (brief + "\n\nYour previous program:\n```python\n" + (draft.program if draft else "") +
                                "```\nIt did not pass the verification:\n- " + "\n- ".join(check.problems) +
                                "\nFix it and reply again in the same format.")
                reply = ask(PROMPT[level].format(key_column=key_column), conversation)
    raise CannotLearn("learn_failed", why=tried[-1] if tried else "")


def _muse(cfg) -> Callable[[str, str], str]:
    def ask(system: str, user: str) -> str:
        from ..brain.client import MetaClient

        r = MetaClient(cfg).chat("recipe", messages=[{"role": "system", "content": system},
                                                     {"role": "user", "content": user}],
                                 max_tokens=8000, reasoning_effort="low")
        return r.choices[0].message.content or ""
    return ask
