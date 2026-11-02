"""Fabricate a machine from a plan, verify it against the user's own demonstration, store it.

Verification uses the demonstration as the test, in two stamps:
  1. block stamp  - each fill_field must leave in its field exactly what the user typed;
  2. record stamp - in a sandbox copy of the app, what the machine saved must equal what the
                    user's submission saved, column by column.
A failing block (an exception, or a field that does not match) is regenerated on its own,
with the error, and the demo is replayed; at most `max_attempts` rounds.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import ROOT, Config
from ..learn import source as src_mod
from . import codegen, runner, sandbox

TOOLS_DIR = ROOT / "tools"


@dataclass
class Verification:
    ok: bool
    attempts: int
    stamps: list[dict] = field(default_factory=list)   # one per attempt: fields + record columns
    repaired: list[str] = field(default_factory=list)  # block ids regenerated


class BuildError(RuntimeError):
    pass


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "machine"


def prepare(plan: dict) -> tuple[src_mod.Sheet, dict]:
    """Load the source sheet; display transforms get the column's Excel number format."""
    sheet = src_mod.load_sheet(Path(plan["source"]["path"]), plan["source"].get("sheet"))
    for b in plan["blocks"]:
        if b["type"] == "fill_field" and b.get("transform", {}).get("kind") == "display":
            b["transform"]["number_format"] = sheet.formats.get(b["column"], "General")
    return sheet, sheet.rows[plan["demo"]["row_index"]]


def page_snapshot(url: str) -> str:
    """Accessibility snapshot of the form page: names and roles as text (cheaper than pixels)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url)
            return page.locator("body").aria_snapshot()
        finally:
            browser.close()


def _same(a: str, b: str) -> bool:
    return " ".join(str(a).split()) == " ".join(str(b).split())


def block_problems(plan: dict, result: runner.RowResult) -> list[tuple[str, str]]:
    """(block id, what went wrong) from one replay of the demonstration."""
    if result.error_block or result.error:
        return [(result.error_block or plan["blocks"][0]["id"], result.error or "failed")]
    typed = plan["demo"]["values"]
    problems = []
    for b in plan["blocks"]:
        if b["type"] != "fill_field":
            continue
        label = b["field"]["label"]
        if label not in result.filled:
            problems.append((b["id"], f'it did not record ctx["filled"]["{label}"] (read the field back with input_value())'))
        elif not _same(result.filled[label], typed[label]):
            problems.append((b["id"], f'the field "{label}" ended up as "{result.filled[label]}" '
                                      f'but the user typed "{typed[label]}"'))
    return problems


def blame_column(plan: dict, column: str) -> str:
    """The block responsible for a stored column: the fill_field whose label or sheet column
    shares the most words with it (e.g. amount <- "Amount (PEN)"), else the save block."""
    words = set(re.findall(r"[a-z]+", column.lower()))
    best, best_score = None, 0
    for b in plan["blocks"]:
        if b["type"] != "fill_field":
            continue
        score = len(words & set(re.findall(r"[a-z]+", f'{b["field"]["label"]} {b["column"]}'.lower())))
        if score > best_score:
            best, best_score = b["id"], score
    return best or next(b["id"] for b in plan["blocks"] if b["type"] == "save")


def verify(plan: dict, code: dict[str, str], demo_row: dict, user_record: dict | None,
           on_event=None) -> tuple[list[tuple[str, str]], dict]:
    """Replay the demo once in a sandbox. Returns (problems, stamp)."""
    url = plan["target"]["url"]
    with sandbox.PortalSandbox(url) as sb:
        [result] = runner.run_rows(plan, code, [(plan["demo"]["row_index"], demo_row)], sb.url,
                                   on_event=on_event)
        stored = sb.records()
    stamp = {"fields": [], "record": [], "error": result.error, "error_block": result.error_block}
    for b in plan["blocks"]:
        if b["type"] == "fill_field":
            label = b["field"]["label"]
            got = result.filled.get(label)
            stamp["fields"].append({"block": b["id"], "field": label, "user": plan["demo"]["values"][label],
                                    "machine": got, "match": got is not None and _same(got, plan["demo"]["values"][label])})
    problems = block_problems(plan, result)
    if not problems:
        if len(stored) != 1:
            problems.append((next(b["id"] for b in plan["blocks"] if b["type"] == "save"),
                             f"after the run the app holds {len(stored)} records instead of 1"))
        elif user_record:
            for col, user_v, tool_v, equal in sandbox.compare(user_record, stored[0]):
                stamp["record"].append({"column": col, "user": user_v, "machine": tool_v, "match": equal})
                if not equal and col != "portal_version":
                    problems.append((blame_column(plan, col), f'the app stored {col} = {tool_v!r} but the '
                                                              f'user\'s own submission stored {user_v!r}'))
    return problems, stamp


def fabricate(cfg: Config, plan: dict, client=None, max_attempts: int = 3, log_fn=print,
              on_event=None, code: dict[str, str] | None = None) -> tuple[dict[str, str], Verification]:
    """Write (or, given `code`, re-check) every block, then verify against the demo."""
    if client is None:
        from ..brain.client import MetaClient
        client = MetaClient(cfg)
    url = plan["target"]["url"]
    if not url:
        raise BuildError("I don't know the page address of the form.")
    sheet, demo_row = prepare(plan)
    try:
        user = sandbox.user_record(url, plan["demo"]["end"])
    except Exception:
        user = None
    log_fn(f"  your own submission: {'found' if user else 'NOT found'} in the app's records")
    snapshot = page_snapshot(url)
    code = dict(code) if code else codegen.generate(client, plan, snapshot, demo_row, log_fn=log_fn)
    missing = [b["id"] for b in plan["blocks"] if b["id"] not in code]
    if missing:
        raise BuildError(f"the model wrote no code for {missing}")
    v = Verification(False, 0)
    for attempt in range(1, max_attempts + 1):
        v.attempts = attempt
        bad = []
        for b in plan["blocks"]:
            try:
                codegen.check_code(code[b["id"]])
            except codegen.UnsafeCode as exc:
                bad.append((b["id"], f"the guard rejected the code: {exc}"))
        if bad:
            problems, stamp = bad, {"fields": [], "record": [], "error": bad[0][1], "error_block": bad[0][0]}
        else:
            problems, stamp = verify(plan, code, demo_row, user, on_event)
        stamp["attempt"] = attempt
        v.stamps.append(stamp)
        matched = sum(f["match"] for f in stamp["fields"]) + sum(r["match"] for r in stamp["record"])
        total = len(stamp["fields"]) + len(stamp["record"])
        if not problems:
            v.ok = True
            log_fn(f"  verification {attempt}: PASS - {matched}/{total} checks match your demo")
            break
        log_fn(f"  verification {attempt}: {len(problems)} problem(s)")
        for block_id, problem in problems:
            log_fn(f"    [{block_id}] {problem.splitlines()[0][:160]}")
        if attempt == max_attempts:
            break
        for block_id in dict.fromkeys(b for b, _ in problems):  # each broken block once, in order
            block = next(b for b in plan["blocks"] if b["id"] == block_id)
            problem = "\n".join(p for b, p in problems if b == block_id)
            code[block_id] = codegen.regenerate(client, plan, block, code[block_id], problem, snapshot,
                                                demo_row, log_fn=log_fn)
            v.repaired.append(block_id)
    return code, v


def save_tool(plan: dict, code: dict[str, str], v: Verification) -> Path:
    tool_dir = TOOLS_DIR / slugify(plan["name"])
    (tool_dir / "blocks").mkdir(parents=True, exist_ok=True)
    for b in plan["blocks"]:
        (tool_dir / "blocks" / f"{b['id']}.py").write_text(code[b["id"]] + "\n", encoding="utf-8")
    meta = {**plan, "built_at": time.time(),
            "seconds_per_row_by_hand": round(plan["demo"]["end"] - plan["demo"]["start"], 1)}
    (tool_dir / "plan.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (tool_dir / "verification.json").write_text(json.dumps(
        {"ok": v.ok, "attempts": v.attempts, "repaired": v.repaired, "stamps": v.stamps},
        indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return tool_dir


def load_tool(tool_dir: Path) -> tuple[dict, dict[str, str]]:
    plan = json.loads((tool_dir / "plan.json").read_text(encoding="utf-8"))
    code = {b["id"]: (tool_dir / "blocks" / f"{b['id']}.py").read_text(encoding="utf-8") for b in plan["blocks"]}
    return plan, code


def run_tool(tool_dir: Path, skip_demo_row: bool = True, log_fn=print, on_event=None) -> list[runner.RowResult]:
    """Run a stored machine on the rows of its sheet (by default all but the demonstrated one)."""
    plan, code = load_tool(tool_dir)
    sheet, _ = prepare(plan)
    rows = [(i, r) for i, r in enumerate(sheet.rows) if not (skip_demo_row and i == plan["demo"]["row_index"])]
    runs = tool_dir / "runs"
    runs.mkdir(exist_ok=True)
    log_path = runs / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"

    with open(log_path, "w", encoding="utf-8") as log_file:
        def emit(event: dict) -> None:
            event = {"ts": time.time(), **event}
            log_file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            if event["event"] == "row_out":
                mark = "ok " if event["ok"] else "ERR"
                log_fn(f"  [{mark}] row {event['row'] + 1:>2}: {next(iter(event['filled'].values()), '')}")
            elif event["event"] == "block_error":
                log_fn(f"        block {event['block']} failed: {event['error'].splitlines()[0][:140]}")
            if on_event:
                on_event(event)

        results = runner.run_rows(plan, code, rows, plan["target"]["url"], on_event=emit)
    log_fn(f"  log: {log_path}")
    return results
