"""`pantallita companion`: the inventor on screen, his memory, and the launch gesture.

Do it once by hand; then hold Ctrl, lasso the remaining Excel rows, and throw the deck on the
web page. The inventor catches it, finds (or builds and verifies) the machine, shows the
ghost fan, and runs it when you press Enter."""

from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtCore import QObject, QPointF, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ..config import Config
from ..memory.winutil import window_at
from . import phrases, winenv
from .flow import LaunchFlow
from .launch import LaunchHook, Selection, excel_selection
from .machine import MachineWindow
from .mascot import Mascot
from .stage import FanBar, Stage

log = logging.getLogger(__name__)


def start_memory(cfg: Config, stop: threading.Event) -> list[threading.Thread]:
    """The same recorders as `pantallita run`, as daemon threads of this process."""
    from ..memory.actions import ClickWatcher, FieldWatcher
    from ..memory.audio import AudioRecorder
    from ..memory.capture import PrivacyGate, ScreenCapture
    from ..memory.timeline import Timeline

    timeline = Timeline(cfg)
    timeline.prune()
    gate = PrivacyGate(cfg.capture["interval_s"])
    capture = ScreenCapture(cfg, timeline, stop, gate)
    workers = [capture.run, ClickWatcher(cfg, timeline, stop, gate, on_click=capture.burst).run,
               FieldWatcher(cfg, timeline, stop).run]
    if cfg.audio["enabled"]:
        workers.append(AudioRecorder(cfg, timeline, stop, gate).run)

    def janitor() -> None:
        while not stop.wait(cfg.retention["prune_every_s"]):
            try:
                timeline.prune()
            except Exception:
                log.exception("Prune failed")

    threads = [threading.Thread(target=w, daemon=True) for w in workers + [janitor]]
    for th in threads:
        th.start()
    return threads


class CardCutter(QObject):
    """Turns the lassoed rows into card pictures: a screen crop of the rows, segmented with
    SAM 3.1 (box prompt = the rows' rectangle), sliced into one card per row."""

    ready = Signal(list)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    def cut(self, selection: Selection) -> None:
        threading.Thread(target=self._cut, args=(selection,), daemon=True).start()

    def _cut(self, selection: Selection) -> None:
        try:
            import mss
            import numpy as np
            from PIL import Image

            from ..brain.client import MetaClient
            from ..vision import sam

            l = min(r[0] for r in selection.row_rects)
            t = min(r[1] for r in selection.row_rects)
            r_ = max(r[2] for r in selection.row_rects)
            b = max(r[3] for r in selection.row_rects)
            with mss.MSS() as s:
                shot = s.grab({"left": l, "top": t, "width": r_ - l, "height": b - t})
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            alpha = np.full((img.height, img.width), 255, np.uint8)
            segs = sam.segment(MetaClient(self.cfg), img, box=(2, 2, img.width - 2, img.height - 2),
                               purpose="launch: cards")
            if segs and segs[0].mask is not None:
                m = segs[0].mask.astype(bool)
                x0, y0 = segs[0].box[0], segs[0].box[1]
                alpha[:] = 70  # outside the segmented rows: faded, not gone
                h, w = m.shape
                alpha[y0:y0 + h, x0:x0 + w][m[: img.height - y0, : img.width - x0]] = 255
            rgba = np.dstack([np.asarray(img), alpha])
            cards = []
            for (rl, rt, rr, rb) in selection.row_rects:
                piece = rgba[max(0, rt - t):rb - t, max(0, rl - l):rr - l].copy()
                qimg = QImage(piece.data, piece.shape[1], piece.shape[0], piece.strides[0], QImage.Format_RGBA8888)
                cards.append(qimg.copy())
            self.ready.emit(cards)
        except Exception:
            log.exception("card cutting failed (text cards are used instead)")


class Companion:
    def __init__(self, cfg: Config, app: QApplication):
        self.cfg = cfg
        self.app = app
        self.lang = cfg.ui.get("language", "en")
        self.selection: Selection | None = None
        self.target_rect = (0, 0, 0, 0)
        self.mascot = Mascot()
        self.mascot.clicked.connect(self.on_mascot_clicked)
        self.stage = Stage()
        self.machine = MachineWindow()
        self.fanbar = FanBar()
        self.flow = LaunchFlow(cfg)
        self.cutter = CardCutter(cfg)
        self.hook = LaunchHook()
        self.wire()
        self.hook.start_thread()
        self.mascot.show()
        self.mascot.say(phrases.say("hello", self.lang), 4)

    def wire(self) -> None:
        h, f = self.hook, self.flow
        h.lasso_started.connect(self.stage.start_lasso)
        h.lasso_moved.connect(self.stage.extend_lasso)
        h.lasso_finished.connect(self.on_lasso)
        h.deck_moved.connect(self.stage.move_deck)
        h.thrown.connect(self.on_throw)
        h.cancelled.connect(self.on_cancel)
        self.cutter.ready.connect(self.stage.set_card_images)
        f.say.connect(lambda key, kw: self.mascot.say(phrases.say(key, self.lang, **kw), 5))
        f.mood.connect(lambda mood, secs: self.mascot.set_mood(mood or None, secs))
        f.machine.connect(self.machine.show_machine)
        f.event.connect(self.machine.on_event)
        f.stamps.connect(self.machine.set_stamps)
        f.fan.connect(self.on_fan)
        f.finished.connect(self.on_finished)
        f.show_me_one.connect(lambda: self.mascot.say(phrases.say("show_me_one", self.lang), 7))
        self.fanbar.decided.connect(self.on_decided)

    # --- launch ---------------------------------------------------------------------------------
    def on_lasso(self, points: list) -> None:
        self.stage.end_lasso()
        try:
            self.selection = excel_selection([tuple(p) for p in points])
        except Exception:
            log.exception("reading the Excel selection failed")
            self.selection = None
        if not self.selection:
            self.mascot.set_mood("confused", 3)
            self.mascot.say("I couldn't find rows there.", 3)
            self.hook.reset()
            return
        x, y = points[-1]
        self.stage.make_deck(self.selection.rows, x, y)
        self.hook.arm_deck()
        self.cutter.cut(self.selection)
        self.mascot.say(f"{len(self.selection.rows)} cards! Throw them on the page.", 4)

    def on_throw(self, x: int, y: int) -> None:
        hwnd, _title, process = window_at(x, y)
        if not self.selection or not hwnd or process.lower() == "excel.exe":
            self.on_cancel()
            return
        self.target_rect = winenv.window_rect(hwnd)
        catch_at = QPointF(self.mascot.x - self.stage.geometry().left(), self.mascot.ground_y - 60)
        self.stage.throw_deck(catch_at)
        self.mascot.set_mood("catch", 1.5)
        self.flow.start(self.selection, hwnd, process)

    def on_cancel(self) -> None:
        self.stage.drop_deck()
        self.mascot.say(phrases.say("cancelled", self.lang), 3)

    # --- ghost fan and result -------------------------------------------------------------------
    def on_fan(self, images: list, summary: dict) -> None:
        self.stage.open_fan(images, self.target_rect)
        totals = ", ".join(f"{label}: {value:,.2f}" for label, value in summary.get("totals", {}).items())
        skipped = "".join(f"\nRow {i + 2} will be skipped ({why})." for i, why in summary.get("skipped", []))
        self.fanbar.open(f"<b>Send {summary.get('send', 0)} rows?</b>  {totals}{skipped}")
        self.mascot.say(phrases.say("fan", self.lang), 6)

    def on_decided(self, ok: bool) -> None:
        self.stage.close_fan()
        self.flow.decide(ok)

    def on_finished(self, result: dict) -> None:
        self.hook.reset()
        self.stage.drop_deck()
        if result.get("error"):
            self.mascot.say(phrases.say("failed", self.lang), 6)
            self.machine.finish("Stopped: " + result["error"][:80])
            return
        if result.get("cancelled") or result.get("show_me_one"):
            self.machine.close_machine()
            return
        skipped = phrases.say("skipped", self.lang, n=result["skipped"]) if result.get("skipped") else ""
        self.mascot.say(phrases.say("done", self.lang, ok=result["ok"], skipped=skipped,
                                    saved=phrases.duration(result["saved"], self.lang)), 7)
        self.machine.finish(f'{result["machine"]}: {result["ok"]} sent'
                            + (f', {result["skipped"]} skipped' if result.get("skipped") else "")
                            + (f', {result["failed"]} failed' if result.get("failed") else ""))

    # --- the inventor himself -------------------------------------------------------------------
    def on_mascot_clicked(self) -> None:
        if self.mascot.in_workshop:
            self.mascot.leave_workshop()
            self.mascot.say(phrases.say("back", self.lang), 2.5)
        else:
            avail = self.app.primaryScreen().availableGeometry()
            self.mascot.go_to_workshop(avail.right() - 60)
            self.mascot.say(phrases.say("workshop", self.lang), 2.5)

    def stop(self) -> None:
        self.hook.stop()


def main(cfg: Config, memory: bool = True) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    stop = threading.Event()
    if memory:
        start_memory(cfg, stop)
    companion = Companion(cfg, app)
    try:
        return app.exec()
    finally:
        companion.stop()
        stop.set()
