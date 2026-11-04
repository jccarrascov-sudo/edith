"""Free mode: a request with no recipe, tried as an agent (stage D).

Two executors, always on copies or in dry runs first, at most 25 steps and 3 minutes:
- files (level 1): Muse Spark writes a program `run(files, out_dir)` for the request, given the
  files it names or the recent ones (Excel, Word, CSV); it runs in the isolated process on
  copies; Muse Spark sees what it made (the documents' text) and fixes or finishes. One step =
  one program run.
- desktop (level 4): an agent on the hidden desktop sees the app's controls through UI Automation
  (names, values) and Muse Spark picks one action at a time (set a field, press a button). It
  stops before the final button (a dry run: nothing is saved); after Enter the same actions are
  replayed for real.
After the user applies the result, the task is induced as a recipe when it is a row-of-a-sheet
task: compiled at the lowest level and verified like a demonstration ("Aprendí esta tarea: ...").
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import desktop, docs, sandbox

log = logging.getLogger(__name__)

TITLE_BAR = {"minimizar", "maximizar", "cerrar", "restaurar", "minimize", "maximize", "close", "restore"}
ORDER = None   # set below (a request that asks to make or enter something)
MAX_STEPS = 25
MAX_SECONDS = 180


@dataclass
class Outcome:
    ok: bool
    kind: str                                   # "files" | "desktop"
    made: list[Path] = field(default_factory=list)       # files: what the program created (copies)
    actions: list[dict] = field(default_factory=list)    # desktop: what the agent did
    values: dict = field(default_factory=dict)           # desktop: the fields when it stopped
    steps: int = 0
    seconds: float = 0.0
    why: str = ""                               # when not ok: where it got stuck, one line
    row: dict = field(default_factory=dict)     # {"sheet", "key_column", "key"} when it was a row task
    program: str = ""


# --- files -----------------------------------------------------------------------------------------------

FILES_PROMPT = """You are Edith, doing a task on the user's files for them, as a Python program.
The request: {request}
The files you may read (copies; name -> path is given to run() in `files`):
{files}
Write exactly one top-level `def run(files, out_dir):` that does the task and saves every result as
a NEW file in out_dir (never change the inputs). Allowed imports only: openpyxl, docx (python-docx),
csv, datetime, re, copy, decimal, math, string, unicodedata, collections, itertools, pathlib,
calendar, textwrap, functools, operator. No os, no subprocess, no network, no eval/exec, no names
starting with "__". Return the list of paths you created.
If the task is about ONE row of a sheet (e.g. a letter for one client), say which.
Reply in this format only:
ROW: <key column> = <the row's value>   (or ROW: none)
```python
the program
```"""

FIX_PROMPT = """Here is what your program did (step {step}). Check it against the request. If the result is right
and complete, reply exactly DONE. Otherwise reply with the whole fixed program in the same format
(ROW line, then the ```python block)."""


def _brief(path: Path) -> str:
    try:
        if path.suffix.lower() == ".docx":
            return docs.doc_brief(path)
        if path.suffix.lower() == ".xlsx":
            return docs.sheet_brief(path, limit=8)
        return f"{path.name}:\n" + path.read_text(encoding="utf-8", errors="replace")[:1500]
    except Exception as exc:
        return f"{path.name}: (could not read: {exc})"


def run_files(request: str, files: list[Path], ask: Callable[[list[dict]], str],
              max_steps: int = MAX_STEPS, max_seconds: float = MAX_SECONDS) -> Outcome:
    t0 = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="edith-free-"))
    inp = work / "in"
    inp.mkdir()
    copies = {f.name: str(shutil.copy2(f, inp / f.name)) for f in files}
    listing = "\n".join(f"- {n}:\n{_brief(Path(p))}" for n, p in copies.items())
    messages = [{"role": "user", "content": FILES_PROMPT.format(request=request, files=listing)}]
    program, row = "", {}
    for step in range(1, max_steps + 1):
        if time.monotonic() - t0 > max_seconds:
            return Outcome(False, "files", steps=step - 1, seconds=time.monotonic() - t0,
                           why="out of time (3 min) before the result was right")
        reply = ask(messages)
        messages.append({"role": "assistant", "content": reply})
        if reply.strip().upper().startswith("DONE") and program:
            made = sorted((work / f"out{step - 1}").glob("*"))
            return Outcome(True, "files", made=made, steps=step, seconds=time.monotonic() - t0, row=row, program=program)
        m = re.search(r"```(?:python)?\s*\n(.*?)```", reply or "", re.DOTALL)
        if not m:
            messages.append({"role": "user", "content": "Reply with the ROW line and a ```python program```."})
            continue
        program = m.group(1).strip() + "\n"
        rm = re.search(r"^\s*ROW:\s*(.+?)\s*=\s*(.+?)\s*$", reply, re.MULTILINE)
        row = {"key_column": rm.group(1).strip(), "key": rm.group(2).strip()} if rm and "none" not in rm.group(0).lower() else {}
        out = work / f"out{step}"
        o = sandbox.run_free(program, copies, str(out))
        if not o.ok:
            feedback = f"It failed: {o.error}" + (f" (line {o.line})" if o.line else "") + "\nFix it (same format)."
        else:
            made = sorted(out.glob("*"))
            feedback = (FIX_PROMPT.format(step=step) + "\n\nFiles it made:\n" +
                        ("\n".join(_brief(p) for p in made) if made else "(none)"))
        messages.append({"role": "user", "content": feedback})
    return Outcome(False, "files", steps=max_steps, seconds=time.monotonic() - t0,
                   why=f"no right result after {max_steps} steps")


# --- desktop ---------------------------------------------------------------------------------------------

DESKTOP_PROMPT = """You are Edith, doing a task in a desktop app for the user, one action at a time.
The request: {request}
{data}
You see the app's controls (name: value). Reply with ONE JSON object only:
{{"set": ["<field name>", "<text>"]}} to write a field, {{"press": "<button name>"}} to press a
button, or {{"submit": "<button name>"}} when everything is filled and only the final button that
saves is left (do not press it: the user confirms first). Use the data exactly as it is written in
the sheet, formatted the way the field expects. If you are stuck, {{"stuck": "<why, briefly>"}}."""


def run_desktop(request: str, cmdline: str, title: str, data: str, ask: Callable[[list[dict]], str],
                max_steps: int = MAX_STEPS, max_seconds: float = MAX_SECONDS) -> Outcome:
    """The agent runs in a helper process on the hidden desktop (UI Automation sees only its own
    desktop); this side talks to Muse Spark and sends it one action at a time through files."""
    t0 = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="edith-free-desk-"))
    app = desktop.start_app(cmdline, title, 30)
    helper = desktop.start(f'"{sys.executable}" -m edith.recipes.free agent {app.pid} "{work}"')
    try:
        messages = [{"role": "user", "content": DESKTOP_PROMPT.format(request=request, data=data)}]
        actions: list[dict] = []
        for step in range(1, max_steps + 1):
            if time.monotonic() - t0 > max_seconds:
                return Outcome(False, "desktop", actions=actions, steps=step - 1, seconds=time.monotonic() - t0,
                               why="out of time (3 min)")
            view = _ask_helper(work, {"look": True})
            if view is None:
                return Outcome(False, "desktop", actions=actions, steps=step, why="the app stopped answering")
            messages.append({"role": "user", "content": "Controls now:\n" + "\n".join(f"- {k}: {v}" for k, v in view.items())})
            reply = ask(messages)
            messages.append({"role": "assistant", "content": reply})
            m = re.search(r"\{.*\}", reply or "", re.DOTALL)
            try:
                act = json.loads(m.group(0)) if m else {}
            except json.JSONDecodeError:
                act = {}
            if "submit" in act:
                return Outcome(True, "desktop", actions=actions + [act], values=view, steps=step,
                               seconds=time.monotonic() - t0)
            if "stuck" in act:
                return Outcome(False, "desktop", actions=actions, steps=step, seconds=time.monotonic() - t0,
                               why=str(act["stuck"])[:150])
            if "set" not in act and "press" not in act:
                messages.append({"role": "user", "content": "Reply with one JSON action only."})
                continue
            res = _ask_helper(work, act)
            if res is None or res.get("error"):
                messages.append({"role": "user", "content": f"That failed: {(res or {}).get('error', 'no answer')}"})
                continue
            actions.append(act)
        return Outcome(False, "desktop", actions=actions, steps=max_steps, seconds=time.monotonic() - t0,
                       why=f"not finished after {max_steps} steps")
    finally:
        _ask_helper(work, {"quit": True}, timeout_s=3)
        helper.kill()
        app.kill()


def replay(cmdline: str, title: str, actions: list[dict]) -> list[str]:
    """After Enter: the same actions for real, the final button pressed, on the hidden desktop."""
    work = Path(tempfile.mkdtemp(prefix="edith-free-real-"))
    app = desktop.start_app(cmdline, title, 30)
    helper = desktop.start(f'"{sys.executable}" -m edith.recipes.free agent {app.pid} "{work}"')
    errors = []
    try:
        for act in actions:
            act = {"press": act["submit"]} if "submit" in act else act
            res = _ask_helper(work, act)
            if res is None or res.get("error"):
                errors.append(str((res or {}).get("error", "no answer")))
        time.sleep(0.8)
    finally:
        _ask_helper(work, {"quit": True}, timeout_s=3)
        helper.kill()
        app.kill()
    return errors


def _ask_helper(work: Path, cmd: dict, timeout_s: float = 30) -> dict | None:
    n = len(list(work.glob("cmd-*.json")))
    (work / f"cmd-{n}.tmp").write_text(json.dumps(cmd, ensure_ascii=False), encoding="utf-8")
    (work / f"cmd-{n}.tmp").replace(work / f"cmd-{n}.json")
    end = time.monotonic() + timeout_s
    answer = work / f"ans-{n}.json"
    while time.monotonic() < end:
        if answer.exists():
            try:
                return json.loads(answer.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        time.sleep(0.05)
    return None


def _agent(pid: int, work: Path) -> None:
    """The helper on the hidden desktop: looks at and acts on the app through UI Automation."""
    from .uihelp import FormUI

    ui = FormUI(pid)
    n = 0
    while True:
        cmd_file = work / f"cmd-{n}.json"
        while not cmd_file.exists():
            time.sleep(0.03)
        cmd = json.loads(cmd_file.read_text(encoding="utf-8"))
        try:
            if cmd.get("quit"):
                return
            if cmd.get("look"):
                ui._index()
                res = {}
                for (kind, name), c in ui._cache.items():
                    if kind in ("EditControl", "ComboBoxControl"):
                        res[name] = c.GetValuePattern().Value or ""
                    elif kind == "ButtonControl" and name.lower() not in TITLE_BAR:
                        res[name] = "(button)"
            elif "set" in cmd:
                ui.set(cmd["set"][0], cmd["set"][1])
                res = {"ok": True}
            elif "press" in cmd:
                ui.invoke(cmd["press"])
                res = {"ok": True}
            else:
                res = {"error": "unknown action"}
        except Exception as exc:
            res = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        (work / f"ans-{n}.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        n += 1


ORDER = re.compile(r"^\W*(edith\W+)?(por favor\W+)?(haz(me)?|crea(me)?|genera|prepara|arma|registra|llena|ingresa|"
                   r"pon|escribe|calcula|lista|make|create|generate|prepare|build|enter|register|fill|write|list|compute)\b",
                   re.IGNORECASE)


def app_for(request: str, apps: dict[str, str]) -> tuple[str, str] | None:
    """(window title, command line) of a known app the request names ("en Northwind ...")."""
    words = set(re.findall(r"\w{4,}", docs.fold(request)))
    for title, cmd in apps.items():
        if words & set(re.findall(r"\w{4,}", docs.fold(title.split(" - ")[0]))):
            return title, cmd
    return None


def inputs_for(request: str, recent: list[tuple[Path, float]]) -> list[Path]:
    """The files a request works on: those it names, else the recent sheets and documents."""
    named = [p for p, _t in recent if docs.fold(p.stem) in docs.fold(request)]
    if named:
        return named[:6]
    out, seen = [], set()
    for p, _t in sorted(recent, key=lambda x: -x[1]):
        if p.suffix.lower() in (".xlsx", ".docx", ".csv") and not p.name.startswith("~$") and p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out[:4]


def induce_files(cfg, store, outcome: Outcome, inputs: list[Path], applied: list[Path]):
    """A row task that made a Word document from a template: compile it like a demonstration."""
    from . import learn

    sheet = next((p for p in inputs if p.suffix.lower() == ".xlsx"), None)
    template = next((p for p in inputs if p.suffix.lower() == ".docx"), None)
    doc = next((p for p in applied if p.suffix.lower() == ".docx"), None)
    if not (outcome.row and sheet and template and doc):
        return None
    return learn.compile_demo(cfg, learn.Demo(sheet, template, doc, outcome.seconds), store=store)


def induce_desktop(cfg, store, outcome: Outcome, cmdline: str, title: str, sheet: Path | None):
    """The fields the agent filled are a demonstration: compile it at level 4 (verified dry)."""
    from . import forms

    if sheet is None:
        return None
    typed = {k: v for k, v in outcome.values.items() if v != "(button)"}
    submit = next((a["submit"] for a in outcome.actions if "submit" in a), "")
    demo = forms.FormDemo(cmdline, title, "", typed, submit, sheet, outcome.seconds, forms.state_files(cmdline))
    return forms.compile_form(cfg, demo, store=store, levels=(4, 5))


def muse_chat(cfg) -> Callable[[list[dict]], str]:
    def ask(messages: list[dict]) -> str:
        from ..brain.client import MetaClient

        r = MetaClient(cfg).chat("free", messages=messages, max_tokens=6000, reasoning_effort="low")
        return r.choices[0].message.content or ""
    return ask


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "agent":
    _agent(int(sys.argv[2]), Path(sys.argv[3]))
