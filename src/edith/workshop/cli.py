"""`edith build` / `edith machine`: fabricate a machine from a plan, run stored ones."""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import Config
from ..learn.plan import PLANS_DIR
from . import build

YES = ("", "y", "yes", "s", "si", "sí")


def yes(answer: str) -> bool:
    # Piped input on Windows may start with a BOM.
    return answer.replace("\ufeff", "").strip().lower() in YES


def latest_plan() -> Path | None:
    plans = sorted(PLANS_DIR.glob("plan-*.json"))
    return plans[-1] if plans else None


def show_stamps(v: build.Verification) -> None:
    """The two overlapping stamps, as text: one mark per field and stored column, per row."""
    last = v.stamps[-1] if v.stamps else {}
    for cs in last.get("cases", []):
        marks = "".join("✓" if c["match"] else "✗" for c in cs["fields"])
        record = "".join("✓" if c["match"] else "✗" for c in cs["record"])
        print(f"    {cs['case']:<14} fields {marks}  record {record}")


def build_and_run(cfg: Config, plan_path: Path | None = None, confirm: bool = True) -> int:
    plan_path = plan_path or latest_plan()
    if not plan_path:
        print("There is no plan yet: show me a task and ask me to do the rest.")
        return 1
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    print(f'Building "{plan["name"]}" ({len(plan["blocks"])} blocks)...')
    try:
        code, v = build.fabricate(cfg, plan)
    except (build.BuildError, build.sandbox.NoSandbox) as exc:
        print(f"Hmm. {exc}")
        return 1
    show_stamps(v)
    if not v.ok:
        print(f"I couldn't make it match your demo after {v.attempts} tries. Sparks everywhere... sorry!")
        return 1
    tool_dir = build.save_tool(plan, code, v)
    repaired = f", fixed {', '.join(v.repaired)} on the way" if v.repaired else ""
    print(f'"{plan["name"]}" passed on attempt {v.attempts}{repaired}. On the shelf: {tool_dir}')
    print("Compiling down to the network level...")
    build.compile_down(tool_dir)
    return run_machine(cfg, tool_dir, confirm=confirm, proof_checked=True)


def repair_from_proof(cfg: Config, tool_dir: Path) -> bool:
    """The proof failed: repair by intent (relocate moved fields, regenerate only broken
    blocks), verified again against the stored demo."""
    print("  repairing against your demo...")
    r = build.repair(cfg, tool_dir, allow=cfg.network["allow"])
    for x in r.relocated:
        print(f'  [{x.block_id}] "{x.old_label}" -> "{x.new_label}" ({x.how})')
    for x in r.ambiguous:
        print(f'  [{x.block_id}] "{x.column}" could go in: {", ".join(x.candidates)}. Show me one.')
    if r.ok:
        print(f"  repaired: {', '.join([x.block_id for x in r.relocated] + r.regenerated) or r.message}")
    return r.ok


def run_machine(cfg: Config, tool_dir: Path, confirm: bool = True, proof_checked: bool = False) -> int:
    plan, _ = build.load_tool(tool_dir)
    if not proof_checked:
        print(f'Checking "{plan["name"]}" against its proof first...')
        ok, _problems, _stamp = build.dry_run(tool_dir, allow=cfg.network["allow"])
        if not ok:
            print("The proof failed, so I won't run it like this.")
            if not repair_from_proof(cfg, tool_dir):
                print("I couldn't repair it on my own. Show me one and I'll rebuild it.")
                return 1
            plan, _ = build.load_tool(tool_dir)
    rows = build.rows_to_run(plan)
    s = build.summary(plan, rows)
    totals = ", ".join(f"{label} total {value:,.2f}" for label, value in s["totals"].items())
    print(f"Ready to send {s['send']} rows" + (f" ({totals})" if totals else "") + ".")
    for i, reason in s["skipped"]:
        print(f"  row {i + 1} will be skipped: {reason}")
    if confirm and not yes(input("Send them? [Y/n] ")):
        print("Okay, nothing sent. The machine is on the shelf.")
        return 0
    level = build.level_for(tool_dir, cfg.network["allow"])
    print(f'Running "{plan["name"]}" at the {level} level...')
    t0 = time.monotonic()
    results = build.run_tool(tool_dir, allow=cfg.network["allow"])
    ok = sum(r.ok for r in results)
    skipped = sum(bool(r.skipped) for r in results)
    failed = len(results) - ok - skipped
    seconds = time.monotonic() - t0
    by_hand = plan.get("seconds_per_row_by_hand", 0) * ok
    print(f"{ok} sent, {skipped} skipped (reported), {failed} failed, in {seconds:.0f}s "
          f"(by hand: ~{by_hand / 60:.0f} min).")
    return 0 if failed == 0 else 1


def find_tool(name: str) -> Path | None:
    if not build.TOOLS_DIR.exists():
        return None
    slug = build.slugify(name)
    for d in sorted(build.TOOLS_DIR.iterdir()):
        if d.is_dir() and (d / "plan.json").exists() and (d.name == slug or slug in d.name):
            return d
    return None


def list_tools() -> None:
    dirs = [d for d in sorted(build.TOOLS_DIR.iterdir()) if (d / "plan.json").exists()] if build.TOOLS_DIR.exists() else []
    if not dirs:
        print("The shelf is empty.")
    for d in dirs:
        plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
        has_proof = "proof" if (d / "proof" / "expected.json").exists() else "NO proof"
        if (d / "network.json").exists():
            has_proof += ", network level"
        print(f'  {d.name:<24} "{plan["name"]}" ({has_proof}): {plan["intent"]}')
