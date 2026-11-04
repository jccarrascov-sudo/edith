"""Escalation: when a recipe fails on a row twice, Muse Spark takes over.

1. Replan from the current state: Muse Spark gets the program, the error (with its line) and the
   row it failed on, and fixes the program. The fix is verified like any program: the recipe's
   proof must still give the user's document, the failing row must now work, and two more rows
   too (no row values as literals).
2. If that fails too, the recipe goes down the cascade: it is compiled again from its proof at the
   next level (level 1 -> 2: Word through COM), with the same verification plus the failing row.
3. When no level is left, it stops and says why in one line.
A fix that passes is kept as a new version of the recipe (the previous one stays, like lessons).
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from . import docs, sandbox
from .store import Recipe, RecipeStore

log = logging.getLogger(__name__)

REPLAN = """A Python program that redoes a person's task for any row of their Excel sheet (it fills a Word
template and saves a new document) just failed twice on one row. Fix the program from what you
see: the error, the line, the row's values. The fix must work for that row AND keep working
for every other row exactly as before (the person's own document must stay identical).
Keep the rules: one top-level `def run({signature}):`, the same allowed imports (openpyxl, docx,
datetime, re, copy, decimal, math, string, unicodedata, collections, itertools, pathlib,
calendar, textwrap, functools, operator), no other modules, no eval/exec, no dunder names, no
values of a row written as literals. If a value cannot be read, leave it blank rather than fail.

Reply with the whole fixed program only:
```python
the program
```"""


class GaveUp(Exception):
    """No level could do it; `line` says why, for the panel."""

    def __init__(self, line: str):
        super().__init__(line)
        self.line = line


def _rows(r: Recipe):
    _h, rows = docs.sheet_rows(r.sheet)
    return rows


def _row_of(r: Recipe, key: str) -> dict | None:
    return next((x for x in _rows(r) if docs.fold(str(x.get(r.key_column) or "").strip()) == docs.fold(key)), None)


def check_fix(program: str, level: int, store: RecipeStore, r: Recipe, key: str, work: Path) -> list[str]:
    """[] if the program keeps the proof, works on the failing row and on two more rows."""
    from .learn import literals_from_row

    try:
        sandbox.check(program, level)
    except sandbox.UnsafeProgram as exc:
        return [f"the program breaks a rule: {exc}"]
    row = _row_of(r, key)
    if row is not None:
        bad = literals_from_row(program, row, docs.shape(r.template).text())
        if bad:
            return [f"values of the row are written as literals: {bad}"]
    proof = store.proof(r)
    out = sandbox.run(program, level, str(proof / Path(r.sheet).name), str(proof / Path(r.template).name),
                      r.demo_key, str(work / "proof"))
    if not out.ok:
        return [f"it now fails on the person's own row: {out.error}"]
    diff = docs.compare(proof / Path(r.result).name, out.out)
    if diff:
        return ["the person's own document is no longer identical:"] + diff
    inp = work / "in"
    inp.mkdir(parents=True, exist_ok=True)
    sheet = shutil.copy2(r.sheet, inp / Path(r.sheet).name)
    template = shutil.copy2(r.template, inp / Path(r.template).name)
    others = [str(x.get(r.key_column)).strip() for x in _rows(r)
              if str(x.get(r.key_column) or "").strip() and docs.fold(str(x.get(r.key_column)).strip()) != docs.fold(key)]
    for i, k in enumerate([key] + others[:1] + others[-1:]):
        o = sandbox.run(program, level, str(sheet), str(template), k, str(work / f"k{i}"))
        if not o.ok:
            return [f'it fails on the row "{k}": {o.error}' + (f" (line {o.line})" if o.line else "")]
        if docs.fold(docs.squash(k)) not in docs.fold(docs.shape(o.out).text()):
            return [f'with the row "{k}" its value does not appear in the document']
    return []


def _keep(store: RecipeStore, r: Recipe, program: str, level: int) -> Recipe:
    from .lessons import save_version

    save_version(store, r)
    r.level = level
    store.save(r, program)
    return r


def escalate(cfg, store: RecipeStore, r: Recipe, key: str, error: str, line: int | None,
             ask: Callable[[str, str], str] | None = None,
             on_status: Callable[[str, dict], None] = lambda k, kw: None) -> Recipe:
    """A recipe that can do `key` (kept as a new version), or GaveUp."""
    from .learn import LEVELS, Demo, _muse, compile_demo, CannotLearn

    ask = ask or _muse(cfg)
    program = store.program(r)
    row = _row_of(r, key)
    # 1. replan at the same level
    on_status("escalate_replan", {})
    brief = "\n\n".join([
        docs.sheet_brief(r.sheet, highlight=row), "The program:\n```python\n" + program + "```",
        f'It failed twice on the row "{key}" with: {error}' + (f" (line {line} of the program)" if line else "")])
    message = brief
    for attempt in range(2):
        m = re.search(r"```(?:python)?\s*\n(.*?)```", ask(REPLAN.format(signature=", ".join(sandbox.SIGNATURE[r.level])),
                                                        message) or "", re.DOTALL)
        work = Path(tempfile.mkdtemp(prefix="edith-escalate-"))
        try:
            problems = ["the reply had no ```python program```"] if not m else \
                check_fix(m.group(1).strip() + "\n", r.level, store, r, key, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        log.info("escalation: replan attempt %d at level %d: %s", attempt + 1, r.level, problems[:1] or "fixed")
        if not problems:
            return _keep(store, r, m.group(1).strip() + "\n", r.level)
        message = brief + "\n\nYour fix did not pass:\n- " + "\n- ".join(problems) + "\nFix it again."
    # 2. down the cascade, from the recipe's proof
    proof = store.proof(r)
    demo = Demo(proof / Path(r.sheet).name, proof / Path(r.template).name, proof / Path(r.result).name)
    for level in [lv for lv in LEVELS if lv > r.level]:
        on_status("escalate_level", {"level": level})
        tmp = RecipeStore.__new__(RecipeStore)
        tmp.dir = Path(tempfile.mkdtemp(prefix="edith-escalate-store-"))
        try:
            other = compile_demo(cfg, demo, ask=ask, levels=(level,), store=tmp)
            new_program = tmp.program(other)
        except CannotLearn:
            shutil.rmtree(tmp.dir, ignore_errors=True)
            continue
        work = Path(tempfile.mkdtemp(prefix="edith-escalate-"))
        try:
            problems = check_fix(new_program, level, store, r, key, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(tmp.dir, ignore_errors=True)
        log.info("escalation: level %d: %s", level, problems[:1] or "works")
        if not problems:
            return _keep(store, r, new_program, level)
    raise GaveUp(f'"{key}": {error.splitlines()[0][:120]}')
