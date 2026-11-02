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
from ..learn import plan as plan_mod
from . import codegen, runner, sandbox
from . import proof as proof_mod

TOOLS_DIR = ROOT / "tools"


@dataclass
class Verification:
    ok: bool
    attempts: int
    stamps: list[dict] = field(default_factory=list)   # one per attempt: fields + record columns
    repaired: list[str] = field(default_factory=list)  # block ids regenerated
    user_record: dict | None = None                    # the state change the demo caused


class BuildError(RuntimeError):
    pass


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "machine"


def prepare(plan: dict) -> tuple[src_mod.Sheet, dict]:
    """Load the source sheet; display transforms get the column's Excel number format."""
    plan_mod.ensure_contracts(plan)
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


def block_problems(plan: dict, result: runner.RowResult, expected: dict[str, str] | None = None,
                   who: str = "the user typed") -> list[tuple[str, str]]:
    """(block id, what went wrong) from one replayed row. `expected` maps field label -> text
    the field must hold (default: what the user typed in the demonstration)."""
    if result.error_block or result.error:
        return [(result.error_block or plan["blocks"][0]["id"], result.error or "failed")]
    expected = expected if expected is not None else plan["demo"]["values"]
    problems = []
    for b in plan["blocks"]:
        if b["type"] != "fill_field":
            continue
        label = b["field"]["label"]
        if label not in result.filled:
            problems.append((b["id"], f'it did not record ctx["filled"]["{label}"] (read the field back with input_value())'))
        elif not _same(result.filled[label], expected[label]):
            problems.append((b["id"], f'the field "{label}" ended up as "{result.filled[label]}" '
                                      f'but {who} "{expected[label]}"'))
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


def planned_fields(plan: dict, row: dict) -> dict[str, str]:
    """What each field should hold for a row, from the plan's transforms (not from the code)."""
    return {b["field"]["label"]: runner.transform(row.get(b["column"]), b["transform"])
            for b in plan["blocks"] if b["type"] == "fill_field"}


@dataclass
class Case:
    """One row to replay in the sandbox, with the state change it must produce."""
    name: str                 # "your demo" / "extra row 6"
    index: int
    row: dict
    fields: dict[str, str]    # field label -> expected text
    record: dict              # stored column -> expected value
    source: str               # where the expectation comes from, for messages


def cases_for(plan: dict, sheet_rows: list[dict] | None, demo_row: dict, user_record: dict,
              extra: int = 2) -> list[Case]:
    """The demo (expected = the user's own submission) plus `extra` more rows whose expected
    record is derived from the demo's relation between sheet cells and stored columns."""
    record = {k: v for k, v in user_record.items() if k not in proof_mod.IGNORED}
    cases = [Case("your demo", plan["demo"]["row_index"], demo_row, dict(plan["demo"]["values"]), record,
                  "your own submission")]
    if sheet_rows:
        relation = proof_mod.relate(user_record, demo_row)
        for i in proof_mod.pick_extra_rows(plan, sheet_rows, extra):
            cases.append(Case(f"extra row {i + 1}", i, sheet_rows[i], planned_fields(plan, sheet_rows[i]),
                              proof_mod.expected_record(relation, sheet_rows[i]), "what your demo implies"))
    return cases


def verify(plan: dict, code: dict[str, str], cases: list[Case], on_event=None) -> tuple[list[tuple[str, str]], dict]:
    """Replay every case in ONE sandbox, in order. Returns (problems, stamp)."""
    url = plan["target"]["url"]
    with sandbox.PortalSandbox(url) as sb:
        results = runner.run_rows(plan, code, [(c.index, c.row) for c in cases], sb.url,
                                  on_event=on_event, stop_on_error=False)
        stored = sb.records()
    save_id = next(b["id"] for b in plan["blocks"] if b["type"] == "save")
    stamp = {"cases": [], "error": None, "error_block": None}
    problems: list[tuple[str, str]] = []
    if len(stored) != sum(r.ok for r in results):
        problems.append((save_id, f"{sum(r.ok for r in results)} rows went through but the app holds "
                                  f"{len(stored)} records"))
    stored_iter = iter(stored)
    for case, result in zip(cases, results):
        cs = {"case": case.name, "row": case.index + 1, "fields": [], "record": [],
              "error": result.error, "error_block": result.error_block}
        for label, want in case.fields.items():
            got = result.filled.get(label)
            cs["fields"].append({"field": label, "expected": want, "machine": got,
                                 "match": got is not None and _same(got, want)})
        who = "the user typed" if case.name == "your demo" else "the plan says"
        case_problems = [(b, f"[{case.name}] {p}") for b, p in block_problems(plan, result, case.fields, who)]
        if result.ok:
            got_record = next(stored_iter, {})
            for col, want in case.record.items():
                if col == "portal_version":
                    continue
                got = got_record.get(col)
                equal = got == want
                cs["record"].append({"column": col, "expected": want, "machine": got, "match": equal})
                if not equal and not case_problems:
                    case_problems.append((blame_column(plan, col), f"[{case.name}] the app stored {col} = "
                                                                  f"{got!r} but {case.source} says {want!r}"))
        if result.error and not stamp["error"]:
            stamp["error"], stamp["error_block"] = result.error, result.error_block
        stamp["cases"].append(cs)
        problems += case_problems
    return problems, stamp


def stamp_counts(stamp: dict) -> tuple[int, int]:
    checks = [c for cs in stamp.get("cases", []) for c in cs["fields"] + cs["record"]]
    return sum(c["match"] for c in checks), len(checks)


def guard_problems(plan: dict, code: dict[str, str], texts: set[str], numbers: set[float]) -> list[tuple[str, str]]:
    bad = []
    for b in plan["blocks"]:
        try:
            codegen.check_code(code[b["id"]])
            codegen.check_anti_cheat(code[b["id"]], b, texts, numbers)
        except codegen.UnsafeCode as exc:
            bad.append((b["id"], f"the guard rejected the code: {exc}"))
    return bad


def fabricate(cfg: Config, plan: dict, client=None, max_attempts: int = 3, log_fn=print,
              on_event=None, code: dict[str, str] | None = None, user_record: dict | None = None,
              extra_rows: int = 2) -> tuple[dict[str, str], Verification]:
    """Write (or, given `code`, re-check) every block, then verify it by equivalence: the
    demo plus `extra_rows` more rows must produce the expected state change. Failing blocks
    are regenerated one by one (max `max_attempts` rounds)."""
    url = plan["target"]["url"]
    if not url:
        raise BuildError("I don't know the page address of the form.")
    sheet, demo_row = prepare(plan)
    if user_record is None:
        try:
            user_record = sandbox.user_record(url, plan["demo"]["end"])
        except Exception:
            user_record = None
        log_fn(f"  your own submission: {'found' if user_record else 'NOT found'} in the app's records")
    if not user_record:
        raise BuildError("I can't find what your demonstration saved, so I have nothing to compare with.")
    cases = cases_for(plan, sheet.rows, demo_row, user_record, extra_rows)
    log_fn(f"  test cases: {', '.join(c.name for c in cases)}")
    texts, numbers = codegen.data_values(sheet.rows, plan["demo"]["values"])
    snapshot = page_snapshot(url)
    if client is None and code is None:
        from ..brain.client import MetaClient
        client = MetaClient(cfg)
    code = dict(code) if code else codegen.generate(client, plan, snapshot, demo_row, log_fn=log_fn)
    missing = [b["id"] for b in plan["blocks"] if b["id"] not in code]
    if missing:
        raise BuildError(f"the model wrote no code for {missing}")
    v = Verification(False, 0)
    for attempt in range(1, max_attempts + 1):
        v.attempts = attempt
        bad = guard_problems(plan, code, texts, numbers)
        if bad:
            problems, stamp = bad, {"cases": [], "error": bad[0][1], "error_block": bad[0][0]}
        else:
            problems, stamp = verify(plan, code, cases, on_event)
        stamp["attempt"] = attempt
        v.stamps.append(stamp)
        matched, total = stamp_counts(stamp)
        if not problems:
            v.ok = True
            log_fn(f"  verification {attempt}: PASS - {matched}/{total} checks over {len(cases)} rows")
            break
        log_fn(f"  verification {attempt}: {len(problems)} problem(s)")
        for block_id, problem in problems:
            log_fn(f"    [{block_id}] {problem.splitlines()[0][:170]}")
        if attempt == max_attempts:
            break
        if client is None:
            from ..brain.client import MetaClient
            client = MetaClient(cfg)
        for block_id in dict.fromkeys(b for b, _ in problems):  # each broken block once, in order
            block = next(b for b in plan["blocks"] if b["id"] == block_id)
            problem = "\n".join(p for b, p in problems if b == block_id)
            code[block_id] = codegen.regenerate(client, plan, block, code[block_id], problem, snapshot,
                                                demo_row, log_fn=log_fn)
            v.repaired.append(block_id)
    v.user_record = user_record
    return code, v


def dry_run(tool_dir: Path, log_fn=print) -> tuple[bool, list[tuple[str, str]], dict]:
    """The machine's own proof, before each use: replay the demo in a sandbox (no model calls)
    and check it still produces the stored state change."""
    plan, code = load_tool(tool_dir)
    loaded = proof_mod.load(tool_dir)
    if not loaded:
        return False, [(plan["blocks"][0]["id"], "this machine has no proof")], {}
    demo, expected = loaded
    case = Case("your demo", demo["row_index"], demo["row"], dict(demo["typed"]), expected["record"],
                "your own submission")
    try:
        sheet_rows = src_mod.load_sheet(Path(plan["source"]["path"]), plan["source"].get("sheet")).rows
    except Exception:
        sheet_rows = [demo["row"]]
    texts, numbers = codegen.data_values(sheet_rows, demo["typed"])
    problems = guard_problems(plan, code, texts, numbers)
    stamp: dict = {}
    if not problems:
        try:
            problems, stamp = verify(plan, code, [case])
        except sandbox.NoSandbox as exc:
            problems = [(plan["blocks"][0]["id"], f"no sandbox: {exc}")]
    matched, total = stamp_counts(stamp) if stamp else (0, 0)
    log_fn(f"  proof: {'PASS' if not problems else 'FAIL'} ({matched}/{total} checks match your demo)")
    for block_id, problem in problems:
        log_fn(f"    [{block_id}] {problem.splitlines()[0][:170]}")
    return not problems, problems, stamp


def save_tool(plan: dict, code: dict[str, str], v: Verification) -> Path:
    """Golden rule: only a machine that passed its equivalence verification enters the workshop."""
    if not v.ok or not v.user_record:
        raise BuildError("a machine that did not pass its verification cannot enter the workshop")
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
    _, demo_row = prepare(plan)
    proof_mod.save(tool_dir, plan, demo_row, v.user_record)
    return tool_dir


def load_tool(tool_dir: Path) -> tuple[dict, dict[str, str]]:
    plan = json.loads((tool_dir / "plan.json").read_text(encoding="utf-8"))
    code = {b["id"]: (tool_dir / "blocks" / f"{b['id']}.py").read_text(encoding="utf-8") for b in plan["blocks"]}
    return plan, code


def rows_to_run(plan: dict, skip_demo_row: bool = True) -> list[tuple[int, dict]]:
    sheet, _ = prepare(plan)
    return [(i, r) for i, r in enumerate(sheet.rows) if not (skip_demo_row and i == plan["demo"]["row_index"])]


def summary(plan: dict, rows: list[tuple[int, dict]]) -> dict:
    """What a run is about to send: rows, rows that will be skipped (and why), numeric totals."""
    send = [(i, r) for i, r in rows if not runner.empty_cells(plan, r)]
    skipped = [(i, "empty " + ", ".join(runner.empty_cells(plan, r))) for i, r in rows if runner.empty_cells(plan, r)]
    totals = {}
    for b in plan["blocks"]:
        if b["type"] == "fill_field" and b["transform"].get("kind") == "number":
            totals[b["field"]["label"]] = round(sum(proof_mod._as_number(r.get(b["column"])) or 0 for _, r in send), 2)
    return {"send": len(send), "skipped": skipped, "totals": totals}


def run_tool(tool_dir: Path, skip_demo_row: bool = True, log_fn=print, on_event=None) -> list[runner.RowResult]:
    """Run a stored machine on the rows of its sheet (by default all but the demonstrated one)."""
    plan, _code = load_tool(tool_dir)
    return run_rows_logged(tool_dir, rows_to_run(plan, skip_demo_row), log_fn=log_fn, on_event=on_event)


def run_rows_logged(tool_dir: Path, rows: list[tuple[int, dict]], log_fn=print, on_event=None) -> list[runner.RowResult]:
    """Run a stored machine on given rows against the real target, logging every event."""
    plan, code = load_tool(tool_dir)
    prepare(plan)
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
            elif event["event"] == "row_skipped":
                log_fn(f"  [-- ] row {event['row'] + 1:>2}: skipped, {event['reason']}")
            elif event["event"] == "block_error":
                log_fn(f"        block {event['block']} failed: {event['error'].splitlines()[0][:140]}")
            if on_event:
                on_event(event)

        results = runner.run_rows(plan, code, rows, plan["target"]["url"], on_event=emit)
    log_fn(f"  log: {log_path}")
    return results
