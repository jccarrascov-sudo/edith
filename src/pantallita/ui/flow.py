"""What happens after a throw, off the UI thread: find (or build) the machine, prove it, preview
it as a ghost fan, wait for the user's OK, run it. Progress goes to the UI through Qt signals."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..config import Config
from ..learn import segment as seg_mod
from ..learn import source as src_mod
from ..memory.timeline import Timeline
from ..workshop import build, runner, sandbox
from ..workshop import proof as proof_mod
from .launch import Selection

log = logging.getLogger(__name__)


def origin(url: str | None) -> str:
    if not url:
        return ""
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def target_url(hwnd: int, process: str) -> str | None:
    import uiautomation as auto

    from ..memory import uia

    if process.lower() not in uia.BROWSERS:
        return None
    with auto.UIAutomationInitializerInThread():
        return uia.address_bar_url(hwnd, budget_s=1.5)


def find_machine(workbook: Path, url: str) -> Path | None:
    """A machine in the workshop that already moves rows of this workbook into this site."""
    if not build.TOOLS_DIR.exists():
        return None
    for d in sorted(build.TOOLS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        plan_file = d / "plan.json"
        if not plan_file.exists():
            continue
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        same_source = Path(plan["source"]["path"]).name.lower() == workbook.name.lower()
        if same_source and origin(plan["target"]["url"]) == origin(url):
            return d
    return None


def find_demo(cfg: Config, workbook: Path, url: str) -> tuple[seg_mod.Segment | None, list[dict]]:
    """The latest episode in memory where values from this workbook were typed into this site."""
    events = Timeline(cfg).read()
    try:
        sheet = src_mod.load_sheet(workbook)
    except Exception:
        return None, events
    for seg in seg_mod.find_demos(events):
        if origin(seg.url) == origin(url) and src_mod.match_row(sheet, seg.final_values):
            return seg, events
    return None, events


class LaunchFlow(QObject):
    say = Signal(str, dict)           # phrase key, format args
    mood = Signal(str, float)         # mascot mood ("" = none), seconds
    machine = Signal(dict, dict)      # plan, code (to draw the machine)
    event = Signal(dict)              # runner / verification events
    stamps = Signal(list)             # verification stamp cases
    fan = Signal(list, dict)          # preview images (PNG bytes), summary
    finished = Signal(dict)           # outcome
    show_me_one = Signal()

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._decision = threading.Event()
        self._confirmed = False
        self.busy = False

    # --- called from the UI thread -------------------------------------------------------------
    def start(self, selection: Selection, hwnd: int, process: str) -> None:
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._run, args=(selection, hwnd, process), daemon=True).start()

    def decide(self, confirmed: bool) -> None:
        self._confirmed = confirmed
        self._decision.set()

    # --- worker ---------------------------------------------------------------------------------
    def _run(self, selection: Selection, hwnd: int, process: str) -> None:
        try:
            self._flow(selection, hwnd, process)
        except Exception as exc:  # never leave the UI waiting
            log.exception("launch flow failed")
            self.mood.emit("confused", 5)
            self.finished.emit({"error": f"{type(exc).__name__}: {exc}"})
        finally:
            self.busy = False

    def _flow(self, selection: Selection, hwnd: int, process: str) -> None:
        self.mood.emit("catch", 1.5)
        self.say.emit("catch", {})
        url = target_url(hwnd, process)
        if not url:
            self.show_me_one.emit()
            self.finished.emit({"error": "the target is not a web page I can read"})
            return
        tool_dir = find_machine(selection.workbook, url)
        if tool_dir:
            plan, _ = build.load_tool(tool_dir)
            self.say.emit("found_machine", {"name": plan["name"]})
        else:
            self.mood.emit("rewind", 3)
            self.say.emit("rewind", {})
            seg, events = find_demo(self.cfg, selection.workbook, url)
            if not seg:
                self.mood.emit("confused", 4)
                self.show_me_one.emit()
                self.finished.emit({"show_me_one": True})
                return
            self.say.emit("found_demo", {"time": time.strftime("%H:%M", time.localtime(seg.start))})
            tool_dir = self._build(seg, events)
            if not tool_dir:
                return
        self._prove(tool_dir)
        self._fan_and_run(tool_dir, selection)

    def _build(self, seg, events) -> Path | None:
        from ..learn import plan as plan_mod

        self.mood.emit("build", 60)
        self.say.emit("building", {})
        plan = plan_mod.make_plan(self.cfg, seg, "do the rest", events, log_fn=log.info)
        self.machine.emit(plan, {})
        self.mood.emit("verify", 60)
        self.say.emit("verifying", {})
        code, v = build.fabricate(self.cfg, plan, log_fn=log.info, on_event=self.event.emit)
        if v.stamps:
            self.stamps.emit(v.stamps[-1].get("cases", []))
        if not v.ok:
            self.mood.emit("confused", 6)
            self.finished.emit({"error": "the machine did not match the demo"})
            return None
        self.say.emit("verified", {})
        return build.save_tool(plan, code, v)

    def _prove(self, tool_dir: Path) -> None:
        plan, code = build.load_tool(tool_dir)
        self.machine.emit(plan, code)
        self.mood.emit("verify", 30)
        ok, _problems, stamp = build.dry_run(tool_dir, log_fn=log.info)
        self.stamps.emit(stamp.get("cases", []))
        if ok:
            return
        self.say.emit("proof_failed", {})
        self.mood.emit("confused", 4)
        _demo, expected = proof_mod.load(tool_dir)
        self.say.emit("repairing", {})
        code, v = build.fabricate(self.cfg, plan, code=code, user_record=dict(expected["record"]),
                                  log_fn=log.info, on_event=self.event.emit)
        if v.stamps:
            self.stamps.emit(v.stamps[-1].get("cases", []))
        if not v.ok:
            raise RuntimeError("the machine failed its proof and could not be repaired")
        build.save_tool(plan, code, v)
        self.machine.emit(plan, code)

    def _fan_and_run(self, tool_dir: Path, selection: Selection) -> None:
        plan, code = build.load_tool(tool_dir)
        build.prepare(plan)  # number formats for display transforms
        rows = list(zip(selection.indexes, selection.rows))
        summary = build.summary(plan, rows)
        complete = [(i, r) for i, r in rows if not runner.empty_cells(plan, r)]
        images = []
        try:
            with sandbox.PortalSandbox(plan["target"]["url"]) as sb:
                images = runner.preview_rows(plan, code, complete[:5], sb.url)
        except Exception:
            log.exception("ghost fan preview failed")
        self.mood.emit("sign", 600)
        self._decision.clear()
        self.fan.emit(images, summary)
        self._decision.wait()
        if not self._confirmed:
            self.mood.emit("", 0)
            self.say.emit("cancelled", {})
            self.finished.emit({"cancelled": True})
            return
        self.mood.emit("build", 600)
        t0 = time.monotonic()
        results = build.run_rows_logged(tool_dir, rows, on_event=self.event.emit)
        ok = sum(r.ok for r in results)
        skipped = sum(bool(r.skipped) for r in results)
        failed = len(results) - ok - skipped
        seconds = time.monotonic() - t0
        saved = max(0.0, plan.get("seconds_per_row_by_hand", 0) * ok - seconds)
        self.mood.emit("" if not failed else "confused", 4)
        self.finished.emit({"ok": ok, "skipped": skipped, "failed": failed, "seconds": seconds,
                            "saved": saved, "machine": plan["name"]})
