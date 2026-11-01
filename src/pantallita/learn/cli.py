"""`pantallita do "..."`: find the demonstration, confirm it with the user, make the plan."""

from __future__ import annotations

import time

from ..config import Config
from ..memory.timeline import Timeline
from . import plan as plan_mod
from . import segment as seg_mod


def do_the_rest(cfg: Config, request: str, confirm: bool = True) -> int:
    events = Timeline(cfg).read()
    demos = seg_mod.find_demos(events)
    if not demos:
        print("Rewinding the film... I don't see a form you filled in the last "
              f"{cfg.retention['minutes']} minutes. Do it once and ask me again!")
        return 1
    now = time.time()
    chosen = seg_mod.pick(demos, request, now)
    print("Rewinding the film... I think it's this:")
    print(f"  {chosen.describe(now)}")
    if confirm:
        others = [d for d in demos if d is not chosen]
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
