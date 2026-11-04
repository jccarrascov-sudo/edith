"""Recipes for desktop forms: levels 4 (UI Automation) and 5 (SAM 3.1 vision).

The demonstration comes from what the memory already records (field values and the click on
the submit button, never the keyboard) plus the Excel row those values came from. Levels 1-3
do not apply: the app keeps its data to itself (no file a program may write, no COM, no web
API), so the cascade starts at level 4 and goes down to 5.

- Level 4: Muse Spark writes `run(sheet, template, key, out_dir, ui)` using ui.set(name, value)
  and ui.invoke(name), by the controls' accessible names.
- Level 5: Edith takes a screenshot of the app on the hidden desktop, SAM 3.1 finds the text
  boxes and buttons, Muse Spark says which is which, and the program uses ui.type(x, y, value)
  and ui.click(x, y) at those points.
- Verification never saves anything: in a dry run the submit button is not pressed; the values
  in the fields at that moment are compared with what the user typed (their own row) and must
  hold each other row's own value (two more rows).
- Running for real (after Enter) presses the button, on the hidden desktop. Undo is offered only
  when the app's data file is known (a file named in its command line): it is backed up first.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import desktop, docs, sandbox
from .store import Recipe, RecipeStore

log = logging.getLogger(__name__)

LEVELS = (4, 5)


@dataclass
class FormDemo:
    cmdline: str                          # how to start the app (from the running process)
    title: str                            # its window title
    process: str
    typed: dict[str, str]                 # field -> what the user typed, in order
    submit: str                           # the button that saved it
    sheet: Path
    manual_s: float = 0.0
    state: list[str] = field(default_factory=list)   # data files named in the command line


def state_files(cmdline: str) -> list[str]:
    """Existing files named in a command line (the app's data, when it says where it is)."""
    out = []
    for tok in re.findall(r'"([^"]+)"|(\S+)', cmdline):
        t = tok[0] or tok[1]
        p = Path(t)
        if p.suffix.lower() in (".ps1", ".exe", ".py", ".bat", ".cmd") or t.startswith("-"):
            continue
        if p.is_file():
            out.append(str(p))
    return out


def command_line_of(title: str) -> str:
    """The command line of the process that owns the visible window with this title."""
    import win32com.client
    import win32gui
    import win32process

    pid = 0

    def visit(h, _):
        nonlocal pid
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) == title:
            pid = win32process.GetWindowThreadProcessId(h)[1]
        return True
    win32gui.EnumWindows(visit, None)
    if not pid:
        return ""
    wmi = win32com.client.GetObject("winmgmts:")
    for proc in wmi.ExecQuery(f"SELECT CommandLine FROM Win32_Process WHERE ProcessId = {pid}"):
        return proc.CommandLine or ""
    return ""


def find_demo(cfg, minutes: float = 30) -> FormDemo | None:
    """The newest form submitted in the memory whose values come from a row of a recent sheet."""
    from ..learn.segment import find_demos
    from ..memory.timeline import Timeline
    from .learn import recent_files

    events = Timeline(cfg).read()
    since = time.time() - minutes * 60
    sheets = sorted(((p, t) for p, t in recent_files(12 * 60) if p.suffix.lower() == ".xlsx"), key=lambda s: -s[1])
    for s in find_demos(events):
        if s.end < since or not s.submit:
            continue
        typed = s.final_values
        for sheet, _t in sheets:
            try:
                if demo_row(sheet, typed):
                    title = s.fields[-1].get("window", "")
                    cmd = command_line_of(title)
                    if cmd:
                        return FormDemo(cmd, title, s.process, typed, s.submit["element"]["name"], sheet,
                                        max(0.0, s.end - s.start), state_files(cmd))
            except Exception:
                continue
    return None


def demo_row(sheet: Path, typed: dict[str, str]) -> tuple[list[dict], dict, str] | None:
    """(rows, the row the typed values came from, its key column), or None."""
    from .learn import value_forms

    _h, rows = docs.sheet_rows(sheet)
    values = {docs.fold(docs.squash(v)) for v in typed.values() if str(v).strip()}
    best, best_n = None, 0
    for r in rows:
        n = sum(1 for v in r.values() if any(docs.fold(f) in values for f in value_forms(v)))
        if n > best_n:
            best, best_n = r, n
    if best is None or best_n < 2:
        return None
    keys = []
    for pos, col in enumerate(best):
        v = best[col]
        if not isinstance(v, str) or docs.fold(docs.squash(v)) not in values:
            continue
        col_values = [docs.fold(docs.squash(str(r.get(col) or ""))) for r in rows]
        filled = [x for x in col_values if x]
        if col_values.count(docs.fold(docs.squash(v))) == 1:
            keys.append((len(set(filled)) == len(filled), -pos, col))
    return (rows, best, max(keys)[2]) if keys else None


# --- the app on the hidden desktop -----------------------------------------------------------------

class App:
    """One instance of the app on the hidden desktop, ended on exit."""

    def __init__(self, cmdline: str, title: str, state_map: dict[str, str] | None = None):
        for old, new in (state_map or {}).items():
            cmdline = cmdline.replace(old, new)
        self.cmdline, self.title = cmdline, title

    def __enter__(self):
        self.p = desktop.start_app(self.cmdline, self.title, 30)
        return self

    def __exit__(self, *exc):
        self.p.kill()


def dry_run(program: str, level: int, demo_or_recipe, key: str, work: Path, points: dict | None = None) -> tuple[dict | None, str]:
    """({field: value} at the moment of submitting, "") or (None, why). Nothing is saved: the app
    runs on copies of its data files, and the submit button is not pressed."""
    cmdline, title, submit, sheet, state = _parts(demo_or_recipe)
    work.mkdir(parents=True, exist_ok=True)
    copies = {}
    for i, f in enumerate(state):
        c = work / f"state{i}{Path(f).suffix}"
        shutil.copy2(f, c)
        copies[f] = str(c)
    with App(cmdline, title, copies) as app:
        out = sandbox.run(program, level, str(sheet), "", key, str(work / "out"), pid=app.p.pid, dry=submit, points=points)
    if not out.ok:
        return None, out.error + (f" (line {out.line})" if out.line else "")
    sub = work / "out" / "submitted.json"
    if not sub.exists():
        return None, f'the program never pressed "{submit}"'
    return json.loads(sub.read_text(encoding="utf-8"))["values"], ""


def _parts(x) -> tuple[str, str, str, Path, list[str]]:
    if isinstance(x, FormDemo):
        return x.cmdline, x.title, x.submit, x.sheet, x.state
    e = x.extra
    return x.template, e["title"], e["submit"], Path(x.sheet), e.get("state", [])


def same_values(typed: dict[str, str], got: dict[str, str]) -> list[str]:
    problems = []
    for name, want in typed.items():
        have = got.get(name)
        if have is None:
            problems.append(f'the field "{name}" was not found when submitting')
        elif docs.squash(have) != docs.squash(want):
            problems.append(f'"{name}": expected "{want}", got "{have}"')
    return problems


def verify(program: str, level: int, demo: FormDemo, rows: list[dict], row: dict, key_column: str,
           points: dict | None = None) -> list[str]:
    try:
        sandbox.check(program, level)
    except sandbox.UnsafeProgram as exc:
        return [f"the program breaks a rule: {exc}"]
    from .learn import literals_from_row

    bad = literals_from_row(program, row, "")
    if bad:
        return [f"values of the row are written as literals: {bad}; read them from the row"]
    work = Path(tempfile.mkdtemp(prefix="edith-form-"))
    try:
        got, why = dry_run(program, level, demo, str(row[key_column]).strip(), work / "demo", points)
        if got is None:
            return [f"it failed with the person's own row: {why}"]
        problems = same_values(demo.typed, got)
        if problems:
            return ["the fields differ from what the person typed:"] + problems
        demo_key = docs.fold(docs.squash(str(row[key_column])))
        others = [r for r in rows if r is not row and str(r.get(key_column) or "").strip()]
        for i, other in enumerate(others[:1] + others[-1:]):
            k = str(other[key_column]).strip()
            got, why = dry_run(program, level, demo, k, work / f"o{i}", points)
            if got is None:
                return [f'it failed with the row "{k}": {why}']
            text = docs.fold(" | ".join(got.values()))
            if docs.fold(docs.squash(k)) not in text:
                return [f'with the row "{k}" its value is not in the fields']
            if demo_key not in docs.fold(k) and demo_key in text:
                return [f'with the row "{k}" the fields still say "{row[key_column]}"']
        return []
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- level 5: where the controls are ---------------------------------------------------------------

LABEL = """This is a screenshot of a desktop form. Numbered boxes mark the controls found by a segmentation
model. Say which number is which control. The form's fields are: {fields}; its submit button is
"{submit}". Reply with JSON only: {{"<field or button name>": <number>, ...}}"""


def locate(cfg, demo: FormDemo, ask_vision: Callable | None = None) -> dict[str, list[int]]:
    """{name: [x, y]} in the app's client area, from SAM 3.1 + Muse Spark (vision)."""
    import sys

    from PIL import Image, ImageDraw

    from ..brain.client import MetaClient
    from ..vision import sam

    work = Path(tempfile.mkdtemp(prefix="edith-locate-"))
    shot = work / "form.png"
    with App(demo.cmdline, demo.title, {f: f for f in demo.state}) as app:
        time.sleep(1.0)
        p = desktop.start(f'"{sys.executable}" -m edith.recipes.uihelp capture {app.p.pid} "{shot}"')
        p.wait(30)
    if not shot.exists():
        raise RuntimeError("could not take the screenshot")
    img = Image.open(shot).convert("RGB")
    client = MetaClient(cfg)
    # Measured on the demo form: at 1x, "text box" finds no field (white on light gray, small);
    # at 2x, "white rectangle" finds every field and the button.
    big = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    found: list[tuple[int, int, int, int]] = []
    for prompt in ("white rectangle", "text box", "button"):
        try:
            segs = sam.segment(client, big, text=prompt, purpose="recipe_locate")
        except Exception:
            log.debug("SAM failed for %r", prompt, exc_info=True)
            continue
        for s in segs:
            if s.confidence is not None and s.confidence < 0.3:
                continue
            box = tuple(v // 2 for v in s.box)
            if not any(_iou(box, f) > 0.6 for f in found):
                found.append(box)
    found.sort(key=lambda b: (b[1], b[0]))
    marked = img.copy()
    d = ImageDraw.Draw(marked)
    for i, (l, t, r, b) in enumerate(found):
        d.rectangle((l, t, r, b), outline=(255, 0, 0), width=2)
        d.text((l + 2, t + 1), str(i), fill=(255, 0, 0))
    names = list(demo.typed) + [demo.submit]
    reply = (ask_vision or _muse_vision(cfg))(LABEL.format(fields=", ".join(f'"{n}"' for n in demo.typed),
                                                            submit=demo.submit), marked)
    m = re.search(r"\{.*\}", reply or "", re.DOTALL)
    labels = json.loads(m.group(0)) if m else {}
    picked = [labels.get(n) for n in names if isinstance(labels.get(n), int)]
    if len(picked) != len(set(picked)):           # two names on one box: vision mixed them up
        raise RuntimeError(f"two controls were given the same box: {labels}")
    points = {}
    for name in names:
        i = labels.get(name)
        if isinstance(i, int) and 0 <= i < len(found):
            l, t, r, b = found[i]
            points[name] = [(l + r) // 2, (t + b) // 2]
    missing = [n for n in names if n not in points]
    if missing:
        raise RuntimeError(f"could not locate: {missing}")
    return points


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def _muse_vision(cfg):
    def ask(prompt: str, image) -> str:
        from ..brain import tokens
        from ..brain.client import MetaClient

        url = f"data:image/png;base64,{tokens.png_b64(image)}"
        r = MetaClient(cfg).chat("recipe_locate", messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}],
            max_tokens=1500, reasoning_effort="low")
        return r.choices[0].message.content or ""
    return ask


# --- compiling ---------------------------------------------------------------------------------------

PROMPT = {4: """You turn a task a person did once by hand into a Python program that redoes it for other rows.
The person filled a desktop form with the values of ONE row of an Excel sheet and pressed "{submit}".
Write the program that does the same for any row.
- Exactly one top-level `def run(sheet, template, key, out_dir, ui):` (helpers nested inside).
  sheet = the .xlsx; template and out_dir are not used; key = the value of the column
  "{key_column}" that identifies the row.
- `ui` is the form: ui.set(field_name, text) writes a field, ui.invoke(button_name) presses a button,
  ui.read(field_name) reads one. Use the exact field names below. Nothing else touches the app.
- Allowed imports only: openpyxl, datetime, re, decimal, math, string, unicodedata, collections,
  itertools, functools, operator, calendar, textwrap. No eval/exec, no names starting with "_".
- Read the sheet with openpyxl.load_workbook(sheet, data_only=True); find the row whose
  "{key_column}" equals key (str(value).strip(), case-insensitive).
- Write each field EXACTLY as the person typed it (same number and date format), from the row;
  never write a value of the person's row as a literal. Other rows can differ: accents, amounts as
  numbers or text with commas, dates as datetime or text, empty cells (write "" then).
- Set every field, then press "{submit}" last.
Reply with the program only:
```python
the program
```""",
          5: """You turn a task a person did once by hand into a Python program that redoes it for other rows.
The person filled a desktop form with the values of ONE row of an Excel sheet and pressed "{submit}".
This app cannot be driven by names: you get the screen points of its controls instead.
- Exactly one top-level `def run(sheet, template, key, out_dir, ui):` (helpers nested inside).
  sheet = the .xlsx; template and out_dir are not used; key = the value of the column
  "{key_column}" that identifies the row.
- `ui.type(x, y, text)` replaces the text of the field at that point; `ui.click(x, y)` clicks there.
  The points: {points}
- Allowed imports only: openpyxl, datetime, re, decimal, math, string, unicodedata, collections,
  itertools, functools, operator, calendar, textwrap. No eval/exec, no names starting with "_".
- Read the sheet with openpyxl.load_workbook(sheet, data_only=True); find the row whose
  "{key_column}" equals key (str(value).strip(), case-insensitive).
- Write each field EXACTLY as the person typed it (same number and date format), from the row;
  never write a value of the person's row as a literal. Other rows can differ: accents, amounts as
  numbers or text with commas, dates as datetime or text, empty cells (write "" then).
- Type every field, then click "{submit}" last.
Reply with the program only:
```python
the program
```"""}


def compile_form(cfg, demo: FormDemo, ask: Callable[[str, str], str] | None = None,
                 on_status: Callable[[str, dict], None] = lambda k, kw: None, levels: tuple[int, ...] = LEVELS,
                 store: RecipeStore | None = None, locate_fn: Callable | None = None) -> Recipe:
    from .learn import CORRECTIONS, CannotLearn, _muse

    store = store or RecipeStore(cfg)
    found = demo_row(demo.sheet, demo.typed)
    if found is None:
        raise CannotLearn("learn_no_row", sheet=demo.sheet.name)
    rows, row, key_column = found
    ask = ask or _muse(cfg)
    brief = "\n\n".join([docs.sheet_brief(demo.sheet, highlight=row),
                         f'The form "{demo.title}". What the person typed, field by field:\n' +
                         "\n".join(f'- "{k}": "{v}"' for k, v in demo.typed.items()),
                         f'Key column: "{key_column}" (value in the person\'s row: {row[key_column]!r}).'])
    tried = []
    # Level 5: the vision labelling of SAM's boxes can swap two fields (seen live: "Tax ID" typed into
    # "Client"). Correcting the program cannot fix wrong points, so a failed level 5 locates again once.
    plan = [(lv, r) for lv in levels for r in range(2 if lv == 5 else 1)]
    for level, _round in plan:
        on_status("learn_compiling", {"level": level})
        points = None
        if level == 5:
            for attempt in range(2):                  # one retry: a network or API hiccup is not a verdict
                try:
                    points = (locate_fn or locate)(cfg, demo)
                    break
                except Exception as exc:
                    log.warning("locating the controls failed (attempt %d): %s", attempt + 1, exc)
                    tried.append(f"level 5: {exc}")
            if points is None:
                continue
        system = PROMPT[level].format(submit=demo.submit, key_column=key_column, points=json.dumps(points))
        message = brief
        for attempt in range(CORRECTIONS + 1):
            reply = ask(system, message)
            m = re.search(r"```(?:python)?\s*\n(.*?)```", reply or "", re.DOTALL)
            program = m.group(1).strip() + "\n" if m else ""
            on_status("learn_verifying", {"level": level})
            problems = verify(program, level, demo, rows, row, key_column, points) if m else \
                ["the reply had no ```python program```"]
            log.info("form recipe level %d attempt %d: %s", level, attempt + 1, problems[:2] or "verified")
            if not problems:
                name = demo.title.split(" - ")[0] or demo.process
                r = Recipe(id=RecipeStore.new_id(name), name=name, kind="excel_to_form", level=level,
                           steps=[f"Find the row of {key_column} in {demo.sheet.name}",
                                  f"Fill {', '.join(demo.typed)}", f'Press "{demo.submit}"'],
                           sheet=str(demo.sheet), template=demo.cmdline, result="", key_column=key_column,
                           demo_key=str(row[key_column]).strip(), out_dir="",
                           verified={"level": level, "attempts": attempt + 1, "ts": time.time(), "rows": len(rows)},
                           manual_s=round(demo.manual_s, 1), created=time.time(),
                           extra={"title": demo.title, "submit": demo.submit, "typed": demo.typed,
                                  "state": demo.state, "points": points, "process": demo.process})
                store.save(r, program)
                return r
            tried.append(f"level {level}: {' '.join(problems[:3])}")
            if attempt < CORRECTIONS:
                on_status("learn_correcting", {"level": level, "n": attempt + 1})
                message = (brief + "\n\nYour previous program:\n```python\n" + program + "```\nIt did not pass:\n- " +
                           "\n- ".join(problems) + "\nFix it and reply again in the same format.")
    raise CannotLearn("learn_failed", why=tried[-1] if tried else "")


# --- escalation -----------------------------------------------------------------------------------------

REPLAN = """A Python program that fills a desktop form from a row of an Excel sheet just failed twice on one
row. Fix it from what you see: the error and the row's values. The fix must work for that row AND
keep typing exactly what the person typed for their own row. Keep the same rules: one top-level
`def run(sheet, template, key, out_dir, ui):`, the same `ui` calls, allowed imports only (openpyxl,
datetime, re, decimal, math, string, unicodedata, collections, itertools, functools, operator,
calendar, textwrap), no names starting with "_", no row values as literals; press "{submit}" last.
Reply with the whole fixed program only:
```python
the program
```"""


def demo_of(r: Recipe) -> FormDemo:
    e = r.extra
    return FormDemo(r.template, e["title"], e.get("process", ""), dict(e["typed"]), e["submit"], Path(r.sheet),
                    r.manual_s, list(e.get("state", [])))


def escalate_form(cfg, store: RecipeStore, r: Recipe, key: str, error: str,
                  ask: Callable[[str, str], str] | None = None,
                  on_status: Callable[[str, dict], None] = lambda k, kw: None,
                  locate_fn: Callable | None = None, levels: tuple[int, ...] = LEVELS) -> Recipe:
    """Like recipes.escalate for documents: Muse Spark replans from the error (verified in dry runs:
    the person's row exactly, the failing row, two more); else the next level; else GaveUp."""
    from .escalate import GaveUp
    from .learn import _muse
    from .lessons import save_version

    ask = ask or _muse(cfg)
    demo = demo_of(r)
    found = demo_row(demo.sheet, demo.typed)
    if found is None:
        raise GaveUp(f'"{key}": the demonstration\'s row is no longer in the sheet')
    rows, row, key_column = found
    points = r.extra.get("points")

    def works(program: str, level: int, pts) -> list[str]:
        problems = verify(program, level, demo, rows, row, key_column, pts)
        if problems:
            return problems
        got, why = dry_run(program, level, demo, key, Path(tempfile.mkdtemp(prefix="edith-esc-form-")), pts)
        return [f'it still fails on "{key}": {why}'] if got is None else []

    on_status("escalate_replan", {})
    message = "\n\n".join([docs.sheet_brief(demo.sheet, highlight=row), "The program:\n```python\n" +
                            store.program(r) + "```", f'It failed twice on the row "{key}" with: {error}'])
    for _attempt in range(2):
        m = re.search(r"```(?:python)?\s*\n(.*?)```", ask(REPLAN.format(submit=demo.submit), message) or "", re.DOTALL)
        problems = works(m.group(1).strip() + "\n", r.level, points) if m else ["no program in the reply"]
        log.info("form escalation: replan at level %d: %s", r.level, problems[:1] or "fixed")
        if not problems:
            save_version(store, r)
            store.save(r, m.group(1).strip() + "\n")
            return r
        message += "\n\nYour fix did not pass:\n- " + "\n- ".join(problems)
    for level in [lv for lv in levels if lv > r.level]:
        on_status("escalate_level", {"level": level})
        tmp = RecipeStore.__new__(RecipeStore)
        tmp.dir = Path(tempfile.mkdtemp(prefix="edith-esc-form-store-"))
        try:
            other = compile_form(cfg, demo, ask=ask, levels=(level,), store=tmp, locate_fn=locate_fn)
            program = tmp.program(other)
            pts = other.extra.get("points")
            problems = works(program, level, pts)
        except Exception as exc:
            problems = [str(exc)]
        finally:
            shutil.rmtree(tmp.dir, ignore_errors=True)
        log.info("form escalation: level %d: %s", level, problems[:1] or "works")
        if not problems:
            save_version(store, r)
            r.level = level
            r.extra = {**r.extra, "points": pts}
            store.save(r, program)
            return r
    raise GaveUp(f'"{key}": {error.splitlines()[0][:120] if error else "it failed"}')


# --- running -------------------------------------------------------------------------------------------

def preview(store: RecipeStore, r: Recipe, keys: list[str], work: Path) -> tuple[list[tuple[str, dict]], list[tuple[str, str]]]:
    """Dry runs for each key: ([(key, {field: value})], [(key, why)]). Nothing is saved."""
    program = store.program(r)
    done, failed = [], []
    for i, k in enumerate(keys[:10]):
        got, why = dry_run(program, r.level, r, k, work / f"k{i}", r.extra.get("points"))
        (done.append((k, got)) if got is not None else failed.append((k, why)))
    return done, failed


def submit_for_real(store: RecipeStore, r: Recipe, keys: list[str], work: Path) -> list[str]:
    """After Enter: the same program presses the button, on the hidden desktop. Returns errors."""
    program = store.program(r)
    errors = []
    with App(r.template, r.extra["title"]) as app:
        for i, k in enumerate(keys):
            out = sandbox.run(program, r.level, r.sheet, "", k, str(work / f"real{i}"), pid=app.p.pid,
                              points=r.extra.get("points"))
            if not out.ok:
                errors.append(f"{k}: {out.error}")
        time.sleep(0.8)                      # the app finishes writing
    return errors
