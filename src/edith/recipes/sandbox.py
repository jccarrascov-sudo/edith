"""Running a recipe's program safely.

Two locks, both always on:
1. Before running: the source must be ONE `def run(...)`, importing only document libraries and
   a few pure standard modules, with no eval/exec, no dunder access, no subprocess/os/socket.
2. While running: a separate `python -I` process with an audit hook installed AFTER the
   libraries are imported. It refuses writing, deleting or renaming outside the run's own
   folder, reading outside the folders the recipe uses (and Python's own), starting programs,
   loading native libraries and any network. Timeout 30 s (levels 1-3) or 3 min (levels 4-5),
   and `stop_all()` (the emergency shortcut) kills it at once.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PURE = {"datetime", "re", "copy", "decimal", "math", "string", "unicodedata", "collections", "itertools",
        "pathlib", "calendar", "locale", "textwrap", "functools", "operator"}
LIBS = {0: {"openpyxl", "docx", "csv"}, 1: {"openpyxl", "docx", "csv"}, 2: {"openpyxl", "docx", "csv"}, 4: {"openpyxl", "csv"},
        5: {"openpyxl", "csv"}}
FORBIDDEN = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars", "breakpoint", "input",
             "setattr", "delattr", "memoryview", "help", "exit", "quit"}
SIGNATURE = {1: ["sheet", "template", "key", "out_dir"], 2: ["sheet", "template", "key", "out_dir", "word"],
             4: ["sheet", "template", "key", "out_dir", "ui"], 5: ["sheet", "template", "key", "out_dir", "ui"],
             0: ["files", "out_dir"]}                      # 0 = free mode on files (level-1 libraries)
TIMEOUT = {1: 30, 2: 30, 3: 30, 4: 180, 5: 180}


class UnsafeProgram(ValueError):
    pass


def check(source: str, level: int) -> None:
    """Raise UnsafeProgram unless `source` keeps to the whitelist of its level."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnsafeProgram(f"syntax error: {exc.msg} (line {exc.lineno})") from None
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
    if len(funcs) != 1:
        raise UnsafeProgram("there must be exactly one def run(...)")
    args = [a.arg for a in funcs[0].args.args]
    if args != SIGNATURE[level]:
        raise UnsafeProgram(f"run must take ({', '.join(SIGNATURE[level])})")
    allowed = PURE | LIBS.get(level, set())
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom, ast.Assign, ast.Expr)):
            raise UnsafeProgram(f"only imports, constants and functions at the top level (line {node.lineno})")
        if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant):
            raise UnsafeProgram(f"no statements at the top level (line {node.lineno})")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name.split(".")[0] not in allowed:
                    raise UnsafeProgram(f"import of {name} is not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await, ast.ClassDef)):
            raise UnsafeProgram(f"{type(node).__name__} is not allowed (line {node.lineno})")
        elif isinstance(node, ast.Name) and (node.id in FORBIDDEN or node.id.startswith("__")):
            raise UnsafeProgram(f"{node.id} is not allowed (line {node.lineno})")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__" if level < 4 else "_"):
            raise UnsafeProgram(f"{node.attr} is not allowed (line {node.lineno})")   # (ui's insides too)
        elif isinstance(node, ast.Attribute) and node.attr in ("unlink", "rmdir", "chmod", "symlink_to",
                                                               "hardlink_to", "rmtree"):
            # (rename/replace are also string methods; the audit hook confines renames to the run folder)
            raise UnsafeProgram(f".{node.attr}() is not allowed (line {node.lineno})")


# The child process: imports first, then the audit hook, then the program.
BOOT = r'''
import json, os, sys
job = json.loads(open(sys.argv[1], encoding="utf-8").read()) if len(sys.argv) > 1 else json.loads(sys.stdin.read())
import openpyxl, docx, csv, datetime, re, copy, decimal, math, string, unicodedata, collections, itertools, pathlib
import calendar, locale, textwrap, functools, operator
import docx.oxml, docx.shared, docx.enum.text, openpyxl.styles, openpyxl.utils
word = None
if job["level"] == 2:
    import pythoncom, win32com.client
    pythoncom.CoInitialize()
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    word.DisplayAlerts = 0
if job["level"] >= 4:
    from edith.recipes.uihelp import make
    word = make(job["level"], job["pid"], job.get("dry"), job.get("points"))   # the program's only way to the app (ui)
def norm(p):
    return os.path.normcase(os.path.abspath(p))
WRITE = [norm(p) for p in job["write"]]
RESULT = norm(job["result"]) if job.get("result") else None
READ = [norm(p) for p in job["read"]] + WRITE + [norm(p) for p in {sys.prefix, sys.base_prefix, sys.exec_prefix}]
def inside(p, roots):
    p = norm(p)
    return any(p == r or p.startswith(r + os.sep) for r in roots)
BLOCK = ("subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "os.fork",
         "socket.", "ctypes.", "winreg.", "_winapi.", "urllib.", "http.", "ftplib.", "smtplib.", "webbrowser.",
         "shutil.rmtree", "os.kill", "signal.")
def hook(event, args):
    if event == "open":
        path, mode = args[0], args[1] or "r"
        if isinstance(path, int):
            return
        writes = any(c in str(mode) for c in "wax+")
        if writes and not inside(path, WRITE) and norm(path) != RESULT:
            raise PermissionError(f"Edith: writing outside the run folder is not allowed: {path}")
        if not writes and not inside(path, READ):
            raise PermissionError(f"Edith: reading outside the recipe's folders is not allowed: {path}")
    elif event in ("os.remove", "os.rmdir", "os.rename", "os.replace", "os.mkdir", "os.chmod", "os.truncate"):
        paths = [a for a in args if isinstance(a, (str, bytes, os.PathLike))]
        if not all(inside(p, WRITE) for p in paths):
            raise PermissionError(f"Edith: {event} outside the run folder is not allowed")
    elif job["level"] >= 4 and event.startswith(("ctypes.", "winreg.")):
        return                                          # UI Automation (comtypes) needs them; the program cannot import them
    elif event.startswith(BLOCK) or event == "import" and args[0].split(".")[0] in ("subprocess", "socket", "ctypes", "winreg"):
        raise PermissionError(f"Edith: {event} is not allowed in a recipe")
sys.addaudithook(hook)
ns = {}
try:
    exec(compile(job["source"], "recipe", "exec"), ns)
    args = ([job["args"]["files"], job["args"]["out_dir"]] if job["level"] == 0
            else [job["args"][k] for k in ("sheet", "template", "key", "out_dir")])
    try:
        out = ns["run"](*args, word) if job["level"] in (2, 4, 5) else ns["run"](*args)
    except BaseException as exc:
        if type(exc).__name__ != "Submitted":
            raise
        out = job["dry"]["out"]                         # a dry run stopped at the submit button
    result = {"ok": True, "out": str(out) if out is not None else None}
except BaseException as exc:
    import traceback
    tb = traceback.extract_tb(exc.__traceback__)
    line = next((f.lineno for f in reversed(tb) if f.filename == "recipe"), None)
    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600], "line": line}
finally:
    if word is not None and job["level"] == 2:
        try:
            for d in list(word.Documents):
                d.Close(0)
            word.Quit()
        except Exception:
            pass
if RESULT:
    open(RESULT, "w", encoding="utf-8").write(json.dumps(result))
else:
    sys.stdout.write("\n@@EDITH@@" + json.dumps(result))
'''


@dataclass
class Outcome:
    ok: bool
    out: str | None = None          # the file the program created
    error: str = ""
    line: int | None = None
    seconds: float = 0.0


_running: set[subprocess.Popen] = set()
_lock = threading.Lock()


def stop_all() -> int:
    """The emergency shortcut: kill every running program at once."""
    with _lock:
        procs = list(_running)
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    return len(procs)


class _Hidden:
    """A process on the hidden desktop, for stop_all()."""

    def __init__(self, p):
        self.p = p

    def kill(self):
        self.p.kill()


def _run_hidden(source, level, sheet, template, key, out_dir, read, pid, dry=None, points=None) -> Outcome:
    import tempfile
    import time

    from . import desktop

    t0 = time.monotonic()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jobdir = Path(tempfile.mkdtemp(prefix="edith-job-"))
    (jobdir / "boot.py").write_text(BOOT, encoding="utf-8")
    result = jobdir / "result.json"
    if dry is not None:
        dry = {"submit": dry, "out": str(out / "submitted.json")}
    job = {"source": source, "level": level, "pid": pid, "result": str(result), "dry": dry, "points": points,
           "args": {"sheet": sheet, "template": template, "key": key, "out_dir": str(out)},
           "write": [str(out)], "read": [str(Path(sheet).parent)] + read}
    (jobdir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("MODEL_", "META_"))}
    p = desktop.start(f'"{sys.executable}" -I "{jobdir / "boot.py"}" "{jobdir / "job.json"}"', cwd=str(out), env=env)
    h = _Hidden(p)
    with _lock:
        _running.add(h)
    try:
        code = p.wait(TIMEOUT[level])
        if code is None:
            p.kill()
            return Outcome(False, error=f"timeout after {TIMEOUT[level]} s", seconds=time.monotonic() - t0)
    finally:
        with _lock:
            _running.discard(h)
    seconds = time.monotonic() - t0
    if not result.exists():
        return Outcome(False, error="stopped" if code in (1,) else f"exit code {code}", seconds=seconds)
    res = json.loads(result.read_text(encoding="utf-8"))
    if not res["ok"]:
        return Outcome(False, error=res["error"], line=res.get("line"), seconds=seconds)
    return Outcome(True, out=res.get("out"), seconds=seconds)


def run_free(source: str, files: dict[str, str], out_dir: str) -> Outcome:
    """A free-mode program on copies: run(files, out_dir); it may create several files."""
    import time

    check(source, 0)
    t0 = time.monotonic()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    folders = sorted({str(Path(p).parent) for p in files.values()})
    job = {"source": source, "level": 0, "args": {"files": files, "out_dir": str(out)},
           "write": [str(out)], "read": folders}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("MODEL_", "META_"))}
    proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=str(out), env=env, creationflags=flags,
                            encoding="utf-8", errors="replace")
    with _lock:
        _running.add(proc)
    try:
        stdout, stderr = proc.communicate(json.dumps(job), timeout=TIMEOUT[1])
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return Outcome(False, error=f"timeout after {TIMEOUT[1]} s", seconds=time.monotonic() - t0)
    finally:
        with _lock:
            _running.discard(proc)
    if "@@EDITH@@" not in (stdout or ""):
        return Outcome(False, error=(stderr or "").strip()[-400:] or f"exit code {proc.returncode}",
                       seconds=time.monotonic() - t0)
    res = json.loads(stdout.rsplit("@@EDITH@@", 1)[1])
    if not res["ok"]:
        return Outcome(False, error=res["error"], line=res.get("line"), seconds=time.monotonic() - t0)
    return Outcome(True, out=str(out), seconds=time.monotonic() - t0)


def run(source: str, level: int, sheet: str, template: str, key: str, out_dir: str,
        read: list[str] | None = None, pid: int | None = None, dry: str | None = None,
        points: dict | None = None) -> Outcome:
    import time

    check(source, level)
    if level >= 4:
        return _run_hidden(source, level, sheet, template, key, out_dir, read or [], pid, dry, points)
    t0 = time.monotonic()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    job = {"source": source, "level": level,
           "args": {"sheet": sheet, "template": template, "key": key, "out_dir": str(out)},
           "write": [str(out)], "read": [str(Path(sheet).parent), str(Path(template).parent)] + (read or [])}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("MODEL_", "META_"))}   # no keys
    proc = subprocess.Popen([sys.executable, "-I", "-c", BOOT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=str(out), env=env, creationflags=flags,
                            encoding="utf-8", errors="replace")
    with _lock:
        _running.add(proc)
    try:
        stdout, stderr = proc.communicate(json.dumps(job), timeout=TIMEOUT[level])
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return Outcome(False, error=f"timeout after {TIMEOUT[level]} s", seconds=time.monotonic() - t0)
    finally:
        with _lock:
            _running.discard(proc)
    seconds = time.monotonic() - t0
    if "@@EDITH@@" not in (stdout or ""):
        why = "stopped" if proc.returncode in (1, -9, 9) and not stderr else (stderr or "").strip()[-400:]
        return Outcome(False, error=why or f"exit code {proc.returncode}", seconds=seconds)
    res = json.loads(stdout.rsplit("@@EDITH@@", 1)[1])
    if not res["ok"]:
        return Outcome(False, error=res["error"], line=res.get("line"), seconds=seconds)
    made = res.get("out")
    if not made or not Path(made).is_file() or out.resolve() not in Path(made).resolve().parents:
        # the program may return nothing: take the one new file in its folder
        files = [f for f in out.iterdir() if f.is_file() and f.suffix.lower() in (".docx", ".xlsx", ".csv", ".txt")]
        if len(files) != 1:
            return Outcome(False, error="run() must create one file in out_dir and return its path", seconds=seconds)
        made = str(files[0])
    return Outcome(True, out=made, seconds=seconds)
