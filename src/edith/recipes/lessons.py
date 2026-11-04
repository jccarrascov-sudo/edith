"""Lessons: one-line preferences and corrections, learned when the user corrects a result.

Created (priority 2, the simple version) only when the user
  (a) edits the result in the preview (the edited text is applied at once), or
  (b) writes "corrige: ..." / "correct: ...".
Muse Spark then changes the recipe's program so the correction holds for every row, and the
program is verified again: with (a) the corrected document is the new proof (the row must give
exactly it, two more rows must still work); with (b) the program must change the result and still
work on three rows, and the user sees the new preview before anything is saved. The lesson
itself is one line ("Aprendí: ...") with Discard, which puts the previous program and proof back.
Because the lesson lives in the recipe's program, the next run applies it with no model at all.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from . import docs, sandbox
from .store import Recipe, RecipeStore

CORRECT = re.compile(r"^\s*(corrige|corr[ií]gelo|correcci[oó]n|correct|fix)\s*[:,]\s*(?P<what>.{3,})$", re.IGNORECASE)

_locks: dict[str, threading.Lock] = {}


def lock(recipe_id: str) -> threading.Lock:
    """Held while a recipe learns a lesson; a run of that recipe waits for it."""
    return _locks.setdefault(recipe_id, threading.Lock())


# --- (a) the edit in the preview, applied to the document ---------------------------------------

def _set_text(paragraph, new: str) -> None:
    """Change a paragraph's text keeping its formatting: only the runs the change touches move."""
    old = paragraph.text
    if old == new:
        return
    a = 0
    while a < min(len(old), len(new)) and old[a] == new[a]:
        a += 1
    b = 0
    while b < min(len(old), len(new)) - a and old[len(old) - 1 - b] == new[len(new) - 1 - b]:
        b += 1
    start, end, insert = a, len(old) - b, new[a:len(new) - b]
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(new)
        return
    # a pure insertion goes into the run that holds that position, or starts there ("Bakery|," +
    # " team" joins the plain "," run, not the bold name); at the very end, the last run
    bounds, pos = [], 0
    for run in runs:
        bounds.append((pos, pos + len(run.text)))
        pos += len(run.text)
    home = next((i for i, (r0, r1) in enumerate(bounds) if r0 <= start < r1), len(runs) - 1)
    placed = False
    for i, run in enumerate(runs):
        t = run.text
        r0, r1 = bounds[i]
        touches = (r0 < end and r1 > start) if start != end else i == home
        if not touches:
            continue
        keep_head = t[:max(0, start - r0)]
        keep_tail = t[max(0, end - r0):] if end < r1 else ""
        run.text = keep_head + ("" if placed else insert) + keep_tail
        placed = True
    if not placed:
        runs[-1].text += insert


def _insert_after(ref, text: str):
    from docx.text.paragraph import Paragraph

    el = copy.deepcopy(ref._p)
    ref._p.addnext(el)
    p = Paragraph(el, ref._parent)
    for r in p.runs[1:]:
        r._r.getparent().remove(r._r)
    if p.runs:
        p.runs[0].text = text
        p.runs[0].bold = None if not ref.runs or not ref.runs[0].bold else p.runs[0].bold
    else:
        p.add_run(text)
    return p


def apply_edit(src: str | Path, edited: str, out: str | Path) -> Path:
    """The document `src` with the paragraphs changed to `edited` (one paragraph per line)."""
    import docx

    d = docx.Document(str(src))
    paras = [p for p in d.paragraphs if docs.squash(p.text)]
    old = [docs.squash(p.text) for p in paras]
    new = [docs.squash(line) for line in edited.splitlines() if docs.squash(line)]
    ops = SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in reversed(ops):
        if tag == "equal":
            continue
        n = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(n):
            _set_text(paras[i1 + k], new[j1 + k])
        for k in range(i1 + n, i2):                               # paragraphs taken out
            el = paras[k]._p
            el.getparent().remove(el)
        if j1 + n < j2:                                           # paragraphs added
            ref = paras[i1 + n - 1] if i1 + n - 1 >= 0 else None
            if ref is None or (tag == "insert" and i1 == 0):
                ref = paras[0]
                first = _insert_after(ref, new[j1])
                ref._p.addprevious(first._p)
                ref = first
                start = j1 + 1
            else:
                start = j1 + n
            for j in range(start, j2):
                ref = _insert_after(ref, new[j])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(out))
    return out


# --- the lesson memory ---------------------------------------------------------------------------

@dataclass
class Lesson:
    id: str
    text: str                 # "En los recordatorios, despedirse con «Saludos cordiales»"
    recipe_id: str
    app: str                  # where it applies: "Word"
    source: str               # "preview_edit" | "instruction"
    version: int              # the program/proof version saved before it (Discard goes back there)
    ts: float


class LessonStore:
    def __init__(self, store: RecipeStore):
        self.store = store
        self.path = store.dir.parent / "lessons.jsonl"

    def all(self) -> list[Lesson]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            try:
                out.append(Lesson(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def of(self, recipe_id: str) -> list[Lesson]:
        return [x for x in self.all() if x.recipe_id == recipe_id]

    def for_app(self, app: str) -> list[Lesson]:
        return [x for x in self.all() if x.app == app]

    def add(self, lesson: Lesson) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(lesson), ensure_ascii=False) + "\n")

    def _write(self, lessons: list[Lesson]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(asdict(x), ensure_ascii=False) + "\n" for x in lessons), encoding="utf-8")
        tmp.replace(self.path)

    def discard(self, lesson_id: str) -> int:
        """Forget a lesson: the recipe's program and proof go back to before it (lessons learned
        after it on the same recipe go too, since they were built on it). Returns how many."""
        lessons = self.all()
        x = next((y for y in lessons if y.id == lesson_id), None)
        if x is None:
            return 0
        gone = [y for y in lessons if y.recipe_id == x.recipe_id and y.version >= x.version]
        r = next((y for y in self.store.all() if y.id == x.recipe_id), None)
        if r is not None:
            restore_version(self.store, r, x.version)
        self._write([y for y in lessons if y not in gone])
        return len(gone)

    def drop_recipe(self, recipe_id: str) -> None:
        self._write([y for y in self.all() if y.recipe_id != recipe_id])


def save_version(store: RecipeStore, r: Recipe) -> int:
    """Keep the current program, proof and recipe fields; returns the version number."""
    vdir = store.dir / r.id / "versions"
    n = len(list(vdir.glob("v*"))) + 1 if vdir.exists() else 1
    dst = vdir / f"v{n}"
    dst.mkdir(parents=True)
    shutil.copy2(store.dir / r.id / "program.py", dst / "program.py")
    shutil.copytree(store.proof(r), dst / "proof")
    (dst / "fields.json").write_text(json.dumps({"demo_key": r.demo_key, "result": r.result}, ensure_ascii=False),
                                     encoding="utf-8")
    return n


def restore_version(store: RecipeStore, r: Recipe, n: int) -> None:
    src = store.dir / r.id / "versions" / f"v{n}"
    if not src.exists():
        return
    shutil.copy2(src / "program.py", store.dir / r.id / "program.py")
    shutil.rmtree(store.proof(r), ignore_errors=True)
    shutil.copytree(src / "proof", store.proof(r))
    fields = json.loads((src / "fields.json").read_text(encoding="utf-8"))
    r.demo_key, r.result = fields["demo_key"], fields["result"]
    store.save(r)


# --- changing the program -------------------------------------------------------------------------

UPDATE = """You keep a Python program that redoes a person's task for any row of their Excel sheet (it fills a
Word template and saves a new document). The person just corrected what it made. Change the
program so the correction holds for EVERY row, in general: a value whose format they changed
gets that format for all rows, a sentence they added or changed is added or changed for all,
something they removed goes for all. Keep everything else exactly as it is.
Keep the same rules: one top-level `def run({signature}):`, the same allowed imports (openpyxl,
docx, datetime, re, copy, decimal, math, string, unicodedata, collections, itertools, pathlib,
calendar, textwrap, functools, operator), no other modules, no eval/exec, no dunder names, no
values of a row written as literals (they come from the row).

Reply in this format only:
LESSON: the preference in ONE short line, in the person's language, general (e.g. "En los recordatorios, despedirse con «Saludos cordiales»")
```python
the whole new program
```"""


def _parse(reply: str) -> tuple[str, str] | None:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reply or "", re.DOTALL)
    if not m:
        return None
    lesson = re.search(r"^\s*LESSON:\s*(.+)$", reply, re.MULTILINE)
    return (lesson.group(1).strip() if lesson else ""), m.group(1).strip() + "\n"


def _check(program: str, r: Recipe, key: str, expected: Path | None, before: Path | None, work: Path) -> list[str]:
    """[] if the new program passes; else what is wrong."""
    from .learn import literals_from_row

    try:
        sandbox.check(program, r.level)
    except sandbox.UnsafeProgram as exc:
        return [f"the program breaks a rule: {exc}"]
    _h, rows = docs.sheet_rows(r.sheet)
    row = next((x for x in rows if docs.fold(str(x.get(r.key_column) or "").strip()) == docs.fold(key)), None)
    if row is None:
        return [f'the row "{key}" is no longer in the sheet']
    bad = literals_from_row(program, row, docs.shape(r.template).text())
    if bad:
        return [f"values of the row are written as literals: {bad}; read them from the row"]
    inp = work / "in"
    inp.mkdir(parents=True, exist_ok=True)
    sheet = shutil.copy2(r.sheet, inp / Path(r.sheet).name)
    template = shutil.copy2(r.template, inp / Path(r.template).name)
    out = sandbox.run(program, r.level, str(sheet), str(template), key, str(work / "main"))
    if not out.ok:
        return [f'it failed with the row "{key}": {out.error}' + (f" (line {out.line})" if out.line else "")]
    if expected is not None:
        diff = docs.compare(expected, out.out)
        if diff:
            return ["the result differs from the person's corrected document:"] + diff
    elif before is not None and not docs.compare(before, out.out, check_name=False):
        return ["the program makes the same document as before: the correction was not applied"]
    others = [x for x in rows if x is not row and str(x.get(r.key_column) or "").strip()]
    for i, other in enumerate(others[:1] + others[-1:]):
        k = str(other[r.key_column]).strip()
        o = sandbox.run(program, r.level, str(sheet), str(template), k, str(work / f"o{i}"))
        if not o.ok:
            return [f'it failed with the row "{k}": {o.error}' + (f" (line {o.line})" if o.line else "")]
        if docs.fold(docs.squash(k)) not in docs.fold(docs.shape(o.out).text()):
            return [f'with the row "{k}" its value does not appear in the document']
    return []


def learn(cfg, store: RecipeStore, r: Recipe, key: str, made: Path, *, corrected: Path | None = None,
          instruction: str = "", ask: Callable[[str, str], str] | None = None) -> Lesson | None:
    """Change the recipe's program for a correction; on success save the lesson and return it.
    `made` = what the program made for `key`; `corrected` = the user's edit of it (a), or
    `instruction` = what "corrige: ..." asked (b)."""
    from .learn import CORRECTIONS, _muse

    ask = ask or _muse(cfg)
    program = store.program(r)
    signature = ", ".join(sandbox.SIGNATURE[r.level])
    _h, rows = docs.sheet_rows(r.sheet)
    row = next((x for x in rows if docs.fold(str(x.get(r.key_column) or "").strip()) == docs.fold(key)), None)
    brief = [docs.sheet_brief(r.sheet, highlight=row), "The current program:\n```python\n" + program + "```",
             "What it made for this row:\n" + docs.doc_brief(made)]
    if corrected is not None:
        brief.append("The person's corrected version (match it exactly for this row):\n" + docs.doc_brief(corrected))
    else:
        brief.append(f"The person asked: {instruction}")
    base = "\n\n".join(brief)
    message = base
    with lock(r.id):
        for attempt in range(CORRECTIONS + 1):
            parsed = _parse(ask(UPDATE.format(signature=signature), message))
            work = Path(tempfile.mkdtemp(prefix="edith-lesson-"))
            try:
                problems = ["the reply had no ```python program```"] if parsed is None else \
                    _check(parsed[1], r, key, corrected, made if corrected is None else None, work)
                if not problems:
                    text, new_program = parsed
                    version = save_version(store, r)
                    store.save(r, new_program)
                    proof = store.proof(r)
                    shutil.rmtree(proof, ignore_errors=True)
                    proof.mkdir(parents=True)
                    shutil.copy2(r.sheet, proof / Path(r.sheet).name)
                    shutil.copy2(r.template, proof / Path(r.template).name)
                    new_result = corrected if corrected is not None else Path(next((work / "main").iterdir()))
                    shutil.copy2(new_result, proof / new_result.name)
                    r.demo_key, r.result = key, str(proof / new_result.name)
                    store.save(r)
                    lesson = Lesson(id=f"l{int(time.time() * 1000)}", text=text or instruction or "—", recipe_id=r.id,
                                    app="Word", source="preview_edit" if corrected is not None else "instruction",
                                    version=version, ts=time.time())
                    LessonStore(store).add(lesson)
                    return lesson
            finally:
                shutil.rmtree(work, ignore_errors=True)
            message = (base + "\n\nYour previous program did not pass the check:\n- " + "\n- ".join(problems) +
                       "\nFix it and reply again in the same format.")
    return None
