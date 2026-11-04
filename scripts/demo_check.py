"""Demo-check: the whole recipe flow, N times in a row, OK/FAIL per step.

    .venv\\Scripts\\python scripts\\demo_check.py [--runs 5]

Each run starts from nothing (a new folder with the sheet, the template and the letter the user
made by hand; a new Edith home) and goes through the same Agent the panel uses:
  1. learn       "aprende esto" -> Muse Spark compiles, verification on copies -> level 1 or 2
  2. variation   "haz lo mismo para Panadería Ñandú" -> preview in < 3 s, the letter correct
                 (checked against a letter built independently), nothing saved yet
  3. apply       Enter -> the letter is next to the user's
  4. undo        Undo -> the letter is gone, the user's own letter untouched
  5. ambiguous   "hazlo para Lazy Comet" -> asks which one, runs nothing
  6. several     "para Ñandú y Acuña" -> 2 letters in < 3 s, Esc -> nothing saved
The real Muse Spark API is used for compiling (cost printed); running a recipe calls no model.
Exit code 0 only if every step of every run is OK.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))
sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    os.environ["EDITH_HOME"] = tempfile.mkdtemp(prefix="edith-check-")   # never the user's own memory
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    results = []
    for i in range(args.runs):
        steps = one_run(Path(tempfile.mkdtemp(prefix="edith-check-user-")))
        results.append(steps)
        print(f"\nrun {i + 1}/{args.runs}")
        for name, ok, detail in steps:
            print(f"  {'OK  ' if ok else 'FAIL'} {name:10} {detail}")
    passed = sum(all(ok for _n, ok, _d in s) for s in results)
    print(f"\ndemo-check: {passed}/{args.runs} runs passed")
    return 0 if passed == args.runs else 1


class Driver:
    """Drives the Agent like the panel does: submit, watch its signals, press Enter or Esc."""

    def __init__(self, agent):
        self.agent = agent
        self.replies: list[str] = []
        self.feet: list[str] = []
        self.preview = None
        self.got_preview = threading.Event()
        agent.reply.connect(self.replies.append)
        agent.footnote.connect(self.feet.append)
        agent.result_preview.connect(self._preview)

    def _preview(self, p):
        if p is not None:
            self.preview = p
            self.got_preview.set()

    def do(self, request: str, decide: bool | None = None, timeout: float = 300) -> float:
        """Submit and wait until the agent is free again; Enter/Esc on a preview. Seconds."""
        self.replies.clear()
        self.preview = None
        self.got_preview.clear()
        t0 = time.monotonic()
        self.agent.submit(request)
        end = t0 + timeout
        while self.agent.busy and time.monotonic() < end:
            pump()
            if self.got_preview.is_set() and decide is not None and self.agent.awaiting:
                self.got_preview.clear()
                self.agent.decide(decide)
            time.sleep(0.02)
        pump()
        return time.monotonic() - t0


def one_run(user: Path) -> list[tuple[str, bool, str]]:
    import make_letters
    from PySide6.QtCore import QCoreApplication

    from edith.config import load_config
    from edith.recipes import docs, learn
    from edith.ui import agent as agent_mod

    QCoreApplication.instance() or QCoreApplication([])
    files = make_letters.make(user, hand=True)
    cfg = load_config()
    cfg.root = Path(tempfile.mkdtemp(prefix="edith-check-home-"))     # each run starts from nothing
    cfg.ui["language"] = "es"
    a = agent_mod.Agent(cfg)
    real_find = learn.find_demo
    now = time.time()
    # The user's demonstration as Windows' recent files would list it (opened 95 s apart).
    learn.find_demo = lambda minutes=30: real_find(minutes, [(files["template"], now - 95), (files["sheet"], now - 100),
                                                             (files["result"], now)])
    d = Driver(a)
    out: list[tuple[str, bool, str]] = []
    _h, rows = docs.sheet_rows(files["sheet"])
    by_client = {r["Client"]: r for r in rows}
    spent0 = _spent(cfg)
    try:
        # 1. learn
        s = d.do("Edith, aprende esto")
        recipes = a.recipes.all()
        ok = len(recipes) == 1 and recipes[0].level in (1, 2) and recipes[0].key_column == "Client"
        r = recipes[0] if recipes else None
        out.append(("learn", ok, f"{s:.1f} s, level {r.level if r else '-'}, attempts "
                    f"{r.verified.get('attempts') if r else '-'}, ${_spent(cfg) - spent0:.4f} · "
                    f"{(d.replies or [''])[-1][:70]}"))
        if not ok:
            return out
        # 2. variation
        final = user / "Payment reminder - Panadería Ñandú.docx"
        s = d.do("haz lo mismo para Panadería Ñandú", decide=None, timeout=0.01)
        end = time.monotonic() + 30
        while not d.got_preview.is_set() and time.monotonic() < end:
            pump()
            time.sleep(0.02)
        p = d.preview
        expected = make_letters.hand_letter(by_client["Panadería Ñandú"], Path(tempfile.mkdtemp()))
        diff = docs.compare(expected, p.outputs[0].made) if p else ["no preview"]
        foot = a.t("recipe_foot", level=p.recipe.level, s=f"{p.seconds:.1f}") if p else ""
        ok = p is not None and not diff and p.seconds < 3 and not final.exists()
        out.append(("variation", ok, f"{p.seconds if p else 0:.2f} s · {foot}" + (f" · {diff[0]}" if diff else "")))
        # 3. apply (Enter)
        a.decide(True)
        _wait(a)
        ok = final.exists() and not docs.compare(expected, final)
        out.append(("apply", ok, f"{final.name} {'saved' if final.exists() else 'missing'}"))
        # 4. undo
        mine = files["result"].read_bytes()
        d.do("deshaz")
        ok = not final.exists() and files["result"].read_bytes() == mine
        out.append(("undo", ok, (d.replies or [''])[-1][:60]))
        # 5. ambiguous
        d.do("hazlo para Lazy Comet")
        reply = (d.replies or [""])[-1]
        ok = "Lazy Comet Coffee Bar" in reply and "Lazy Comet Print House" in reply and not any(
            f.name.startswith("Payment reminder - Lazy") for f in user.iterdir())
        out.append(("ambiguous", ok, reply[:80]))
        # 6. several, then Esc
        d.do("para Ñandú y Acuña", decide=False)
        p = d.preview
        ok = (p is not None and len(p.outputs) == 2 and p.seconds < 3 and not final.exists()
              and not (user / "Payment reminder - Librería Acuña.docx").exists())
        out.append(("several", ok, f"{len(p.outputs) if p else 0} letters, {p.seconds if p else 0:.2f} s, Esc -> "
                    f"{(d.replies or [''])[-1][:40]}"))
    except Exception as exc:
        out.append(("error", False, f"{type(exc).__name__}: {exc}"[:200]))
    finally:
        learn.find_demo = real_find
    return out


def pump() -> None:
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.processEvents()


def _wait(a, timeout: float = 30) -> None:
    end = time.monotonic() + timeout
    while a.busy and time.monotonic() < end:
        pump()
        time.sleep(0.02)
    pump()


def _spent(cfg) -> float:
    import json

    from edith.brain.client import Ledger

    return sum(e.get("cost", 0) for e in Ledger().entries())


if __name__ == "__main__":
    raise SystemExit(main())
