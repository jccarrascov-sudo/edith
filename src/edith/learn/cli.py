"""`edith do "..."`: find the demonstration, confirm it with the user, make the plan."""

from __future__ import annotations

import time

from ..config import Config
from ..memory.timeline import Timeline
from . import plan as plan_mod
from . import segment as seg_mod


def spreadsheet_sources(demos: list[seg_mod.Segment], events: list[dict]) -> dict[int, str]:
    """id(episode) -> name of the spreadsheet one of whose rows holds its values."""
    from . import source as src_mod

    titles = [e.get("window", "") for e in events if (e.get("process") or "").lower() == "excel.exe"]
    path = src_mod.find_workbook(titles)
    if not path:
        return {}
    try:
        sheet = src_mod.load_sheet(path)
    except Exception:
        return {}
    return {id(d): path.name for d in demos if src_mod.match_row(sheet, d.final_values)}


def do_the_rest(cfg: Config, request: str, confirm: bool = True) -> int:
    events = Timeline(cfg).read()
    demos = seg_mod.find_demos(events)
    if not demos:
        print("Rewinding the film... I don't see a form you filled in the last "
              f"{cfg.retention['minutes']} minutes. Do it once and ask me again!")
        return 1
    now = time.time()
    client = None
    if len(demos) > 1:
        from ..brain.client import MetaClient
        client = MetaClient(cfg)
    chosen, sure = seg_mod.choose(demos, request, now, client, spreadsheet_sources(demos, events))
    others = [d for d in demos if d is not chosen]
    print("Rewinding the film... " + ("I think it's this:" if sure else "I'm not sure which one. My best guess:"))
    print(f"  {chosen.describe(now)}")
    if confirm and not sure and others:
        for i, d in enumerate([chosen] + others, 1):
            print(f"  {i}. {d.describe(now)}")
        pick = input("Which one? [1] ").strip() or "1"
        chosen = ([chosen] + others)[int(pick) - 1]
    elif confirm:
        answer = input("Is that it? [Y/n" + ("/l = list others" if others else "") + "] ").strip().lower()
        if answer == "l" and others:
            for i, d in enumerate([chosen] + others, 1):
                print(f"  {i}. {d.describe(now)}")
            pick = input("Which one? [1] ").strip() or "1"
            chosen = ([chosen] + others)[int(pick) - 1]
        elif answer not in ("", "y", "yes", "s", "si", "sí"):
            print("Okay! Show me once more and I'll watch closely.")
            return 1
    print("Building the plan...")
    try:
        plan = plan_mod.make_plan(cfg, chosen, request, events)
    except plan_mod.LearnError as exc:
        print(f"Hmm. {exc}")
        return 1
    path = plan_mod.save_plan(plan)
    print("\n".join(plan_mod.describe_plan(plan)))
    print(f"Plan saved to {path}")
    return 0
