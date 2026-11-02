"""Run a machine: every row goes through the blocks, in a headless browser (no mouse).

Events (for the log and for the UI that animates cards and blocks):
  {"event": "run_start", "rows": n}
  {"event": "row_in", "row": i}
  {"event": "block_done", "row": i, "block": id, "ms": t}
  {"event": "block_error", "row": i, "block": id, "error": "..."}
  {"event": "row_out", "row": i, "ok": bool, "filled": {...}}
  {"event": "run_end", "ok": n_ok, "failed": n_failed, "seconds": s}
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable

from ..learn.plan import apply_transform
from . import codegen

Event = dict
EventFn = Callable[[Event], None]


@dataclass
class RowResult:
    index: int
    ok: bool
    filled: dict = field(default_factory=dict)
    error_block: str | None = None
    error: str | None = None
    seconds: float = 0.0


def transform(value, spec: dict) -> str:
    return apply_transform(value, spec, spec.get("number_format", "General"))


def compile_blocks(plan: dict, code: dict[str, str]) -> dict[str, Callable]:
    from playwright.sync_api import expect

    helpers = {"transform": transform, "expect": expect, "re": re}
    return {b["id"]: codegen.compile_block(code[b["id"]], helpers) for b in plan["blocks"]}


def short_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    # Playwright errors carry a long call log; the first lines say what went wrong.
    lines = [line for line in text.splitlines() if line.strip()][:6]
    return "\n".join(lines)[:900]


def run_rows(plan: dict, code: dict[str, str], rows: list[tuple[int, dict]], url: str,
             on_event: EventFn | None = None, headless: bool = True, stop_on_error: bool = True,
             timeout_ms: int = 8000) -> list[RowResult]:
    from playwright.sync_api import sync_playwright

    emit = on_event or (lambda e: None)
    try:
        blocks = compile_blocks(plan, code)
    except codegen.UnsafeCode as exc:
        emit({"event": "run_end", "ok": 0, "failed": len(rows), "seconds": 0, "error": str(exc)})
        raise
    results: list[RowResult] = []
    t_run = time.monotonic()
    emit({"event": "run_start", "rows": len(rows)})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            for index, row in rows:
                result = run_one(page, plan, blocks, index, row, url, emit)
                results.append(result)
                if not result.ok and stop_on_error:
                    break
        finally:
            browser.close()
    emit({"event": "run_end", "ok": sum(r.ok for r in results), "failed": sum(not r.ok for r in results),
          "seconds": round(time.monotonic() - t_run, 2)})
    return results


def run_one(page, plan: dict, blocks: dict[str, Callable], index: int, row: dict, url: str,
            emit: EventFn) -> RowResult:
    t_row = time.monotonic()
    emit({"event": "row_in", "row": index})
    ctx: dict = {"filled": {}}
    try:
        page.goto(url)
    except Exception as exc:
        emit({"event": "block_error", "row": index, "block": None, "error": short_error(exc)})
        return RowResult(index, False, error=f"could not open {url}: {short_error(exc)}")
    for b in plan["blocks"]:
        t0 = time.monotonic()
        try:
            blocks[b["id"]](page, row, ctx)
        except Exception as exc:
            error = short_error(exc)
            emit({"event": "block_error", "row": index, "block": b["id"], "error": error})
            emit({"event": "row_out", "row": index, "ok": False, "filled": ctx.get("filled", {})})
            return RowResult(index, False, ctx.get("filled", {}), b["id"], error, time.monotonic() - t_row)
        emit({"event": "block_done", "row": index, "block": b["id"], "ms": round((time.monotonic() - t0) * 1000)})
    emit({"event": "row_out", "row": index, "ok": True, "filled": ctx.get("filled", {})})
    return RowResult(index, True, ctx.get("filled", {}), seconds=time.monotonic() - t_row)
