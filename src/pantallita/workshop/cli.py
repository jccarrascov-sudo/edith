"""`pantallita build` / `pantallita machine`: fabricate a machine from a plan, run stored ones."""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import Config
from ..learn.plan import PLANS_DIR
from . import build


def latest_plan() -> Path | None:
    plans = sorted(PLANS_DIR.glob("plan-*.json"))
    return plans[-1] if plans else None


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
    if not v.ok:
        print(f"I couldn't make it match your demo after {v.attempts} tries. Sparks everywhere... sorry!")
        return 1
    tool_dir = build.save_tool(plan, code, v)
    repaired = f", fixed {', '.join(v.repaired)} on the way" if v.repaired else ""
    print(f'"{plan["name"]}" passed your own demo on attempt {v.attempts}{repaired}. Saved in {tool_dir}')
    rest = plan["source"]["rows"] - 1
    if confirm and input(f"Run it on the other {rest} rows? [Y/n] ").strip().lower() not in ("", "y", "yes", "s", "si", "sí"):
        print("Okay, it's on the shelf whenever you need it.")
        return 0
    return run_machine(tool_dir)


def run_machine(tool_dir: Path) -> int:
    plan, _ = build.load_tool(tool_dir)
    print(f'Running "{plan["name"]}"...')
    t0 = time.monotonic()
    results = build.run_tool(tool_dir)
    ok = sum(r.ok for r in results)
    seconds = time.monotonic() - t0
    by_hand = plan.get("seconds_per_row_by_hand", 0) * len(results)
    print(f"{ok}/{len(results)} rows done in {seconds:.0f}s "
          f"(by hand: ~{by_hand / 60:.0f} min). " + ("All good!" if ok == len(results) else "Some rows failed."))
    return 0 if ok == len(results) else 1


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
        print(f'  {d.name:<24} "{plan["name"]}": {plan["intent"]}')
